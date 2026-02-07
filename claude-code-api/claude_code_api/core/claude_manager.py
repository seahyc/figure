"""Claude Code process management."""

import asyncio
import json
import os
import subprocess
from collections import deque
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

import structlog

from claude_code_api.models.claude import get_available_models, get_default_model

from .config import settings
from .security import ensure_directory_within_base

logger = structlog.get_logger()


def _extract_text(content: Any) -> str:
    """Extract text from content blocks (text-only fallback)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif "text" in item:
                    parts.append(str(item["text"]))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _convert_to_anthropic_content(content: Any) -> list:
    """Convert OpenAI-format content blocks to Anthropic format.

    OpenAI image format:
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
    Anthropic image format:
      {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content)}]

    result = []
    for item in content:
        if not isinstance(item, dict):
            result.append({"type": "text", "text": str(item)})
            continue

        item_type = item.get("type", "")

        if item_type == "text":
            result.append({"type": "text", "text": item.get("text", "")})

        elif item_type == "image_url":
            # OpenAI format → Anthropic format
            image_url = item.get("image_url", {})
            url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)

            if url.startswith("data:"):
                # Parse data URI: data:image/png;base64,AAAA...
                header, _, b64_data = url.partition(",")
                media_type = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
                result.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                })
            else:
                # URL-based image — pass as-is (Claude may or may not support)
                result.append({
                    "type": "image",
                    "source": {"type": "url", "url": url},
                })

        elif item_type == "image":
            # Already Anthropic format
            result.append(item)

        else:
            # Unknown type — pass through as text
            text = item.get("text", item.get("content", str(item)))
            result.append({"type": "text", "text": str(text)})

    return result


class ClaudeProcess:
    """Manages a single Claude Code process."""

    def __init__(
        self,
        session_id: str,
        project_path: str,
        on_cli_session_id: Optional[Callable[[str], None]] = None,
        on_end: Optional[Callable[["ClaudeProcess"], None]] = None,
    ):
        self.session_id = session_id
        self.cli_session_id: Optional[str] = None
        self.project_path = project_path
        self.process: Optional[asyncio.subprocess.Process] = None
        self.is_running = False
        self.output_queue = asyncio.Queue()
        self.error_queue = asyncio.Queue()
        self._output_task: Optional[asyncio.Task] = None
        self._error_task: Optional[asyncio.Task] = None
        self._on_cli_session_id = on_cli_session_id
        self._on_end = on_end
        self.last_error: Optional[str] = None
        self._stderr_tail: deque[str] = deque(maxlen=20)

    async def start(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        content: Optional[Any] = None,
    ) -> bool:
        """Start Claude Code process and wait for completion.

        Args:
            prompt: Text prompt (used when content is None).
            model: Optional model override.
            system_prompt: Optional system prompt.
            content: Full message content (may include images).
                     When provided, uses --input-format stream-json
                     to send multimodal messages via stdin.
        """
        self.last_error = None
        self._stdin_payload: Optional[str] = None
        try:
            # Detect if content contains images
            has_images = (
                content is not None
                and isinstance(content, list)
                and any(
                    isinstance(item, dict)
                    and item.get("type") in ("image", "image_url")
                    for item in content
                )
            )

            # Build command
            cmd = [settings.claude_binary_path]

            if has_images:
                # Multimodal: read message from stdin in stream-json format
                cmd.extend([
                    "-p",
                    "--input-format", "stream-json",
                    "--output-format", "stream-json",
                    "--verbose",
                    "--dangerously-skip-permissions",
                ])
                # Prepare stdin payload
                anthropic_content = _convert_to_anthropic_content(content)
                stdin_msg = {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": anthropic_content,
                    },
                }
                self._stdin_payload = json.dumps(stdin_msg)
            else:
                # Text-only: pass prompt directly via positional arg
                text = prompt if content is None else _extract_text(content)
                cmd.extend([
                    "-p", text,
                    "--output-format", "stream-json",
                    "--verbose",
                    "--dangerously-skip-permissions",
                ])

            # Add system prompt and model (shared between both modes)
            if system_prompt:
                cmd.extend(["--system-prompt", system_prompt])
            if model:
                cmd.extend(["--model", model])

            logger.info(
                "Starting Claude process",
                session_id=self.session_id,
                project_path=self.project_path,
                model=model or get_default_model(),
                multimodal=has_images,
            )

            # Start process from src directory (where Claude works without API key)
            src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            safe_cmd: List[str] = []
            redact_next = False
            for part in cmd:
                if redact_next:
                    safe_cmd.append("<redacted>")
                    redact_next = False
                    continue
                if part in ("-p", "--system-prompt"):
                    safe_cmd.append(part)
                    redact_next = True
                    continue
                safe_cmd.append(part)
            logger.info(f"Starting Claude from directory: {src_dir}")
            logger.info(f"Command: {' '.join(safe_cmd)}")

            # Start process asynchronously
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=src_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
            )

            # Write multimodal stdin payload if needed, then close stdin
            if self._stdin_payload and self.process.stdin:
                self.process.stdin.write(
                    (self._stdin_payload + "\n").encode()
                )
                self.process.stdin.close()

            self.is_running = True

            # Start background tasks to read output
            self._output_task = asyncio.create_task(self._read_output())
            self._error_task = asyncio.create_task(self._read_error())

            started = await self._verify_startup()
            if not started:
                await self.stop()
                return False

            return True

        except Exception as e:
            self.last_error = str(e)
            await self.stop()
            logger.error(
                "Failed to start Claude process",
                session_id=self.session_id,
                error=str(e),
            )
            return False

    def _decode_output_line(self, line: bytes) -> Optional[Dict[str, Any]]:
        line_text = line.decode().strip()
        if not line_text:
            return None

        payload = line_text
        if payload.startswith("data: "):
            payload = payload[6:].strip()
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {"type": "text", "content": line_text}

    async def _read_output(self):
        """Read stdout from process line by line."""
        claude_session_id = None

        try:
            while self.is_running and self.process:
                line = await self.process.stdout.readline()
                if not line:
                    break

                data = self._decode_output_line(line)
                if not data:
                    continue

                # Extract Claude's session ID from the first message
                if not claude_session_id and data.get("session_id"):
                    claude_session_id = data["session_id"]
                    logger.info(
                        "Extracted Claude session ID", session_id=claude_session_id
                    )
                    self.cli_session_id = claude_session_id
                    if self._on_cli_session_id:
                        self._on_cli_session_id(claude_session_id)

                await self.output_queue.put(data)
        except Exception as e:
            logger.error("Error reading output", error=str(e))
        finally:
            await self.output_queue.put(None)
            self.is_running = False

            logger.info(
                "Claude process output stream ended", session_id=self.session_id
            )
            if self._on_end:
                self._on_end(self)

    async def _read_error(self):
        """Read stderr from process."""
        try:
            while self.is_running and self.process:
                line = await self.process.stderr.readline()
                if not line:
                    break

                error_text = line.decode().strip()
                if error_text:
                    self._stderr_tail.append(error_text)
                    self.last_error = error_text
                    logger.warning("Claude stderr", message=error_text)
        except Exception as e:
            logger.error("Error reading stderr", error=str(e))

    async def _verify_startup(self) -> bool:
        """Detect early process failures so API can return actionable errors."""
        if not self.process:
            self.last_error = "Claude process was not initialized"
            return False

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 1.5
        while loop.time() < deadline:
            return_code = self.process.returncode
            if return_code is None:
                await asyncio.sleep(0.05)
                continue

            if return_code == 0:
                return True

            error_text = self._compose_process_error(return_code)
            self.last_error = error_text
            logger.error(
                "Claude process exited during startup",
                session_id=self.session_id,
                return_code=return_code,
                error=error_text,
            )
            return False

        return True

    def _compose_process_error(self, return_code: int) -> str:
        if self._stderr_tail:
            return f"Claude exited with code {return_code}: {' | '.join(self._stderr_tail)}"
        return f"Claude exited with code {return_code}"

    async def get_output(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Get output from Claude process."""
        while True:
            try:
                # Wait for output with timeout
                output = await asyncio.wait_for(
                    self.output_queue.get(), timeout=settings.streaming_timeout_seconds
                )

                if output is None:  # End signal
                    break

                yield output

            except asyncio.TimeoutError:
                logger.warning("Output timeout", session_id=self.session_id)
                break
            except Exception as e:
                logger.error(
                    "Error getting output", session_id=self.session_id, error=str(e)
                )
                break

    async def send_input(self, text: str):
        """Send input to Claude process."""
        if self.process and self.process.stdin and self.is_running:
            try:
                self.process.stdin.write((text + "\n").encode())
                await self.process.stdin.drain()
            except Exception as e:
                logger.error(
                    "Error sending input", session_id=self.session_id, error=str(e)
                )

    async def stop(self):
        """Stop Claude process."""
        self.is_running = False

        for task in (self._output_task, self._error_task):
            if task and not task.done():
                task.cancel()

        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
            except Exception as e:
                logger.error(
                    "Error stopping process", session_id=self.session_id, error=str(e)
                )
            finally:
                self.process = None
                self._output_task = None
                self._error_task = None

        logger.info("Claude process stopped", session_id=self.session_id)


class ClaudeManagerError(RuntimeError):
    """Base error for Claude manager operations."""


class ClaudeBinaryNotFoundError(ClaudeManagerError):
    """Raised when the Claude binary cannot be located."""


class ClaudeVersionError(ClaudeManagerError):
    """Raised when the Claude version cannot be determined."""


class ClaudeConcurrencyError(ClaudeManagerError):
    """Raised when the concurrent session limit is exceeded."""


class ClaudeProcessStartError(ClaudeManagerError):
    """Raised when a Claude process fails to start."""


class ClaudeSessionConflictError(ClaudeManagerError):
    """Raised when a session already has an active Claude process."""


class ClaudeModelNotSupportedError(ClaudeManagerError):
    """Raised when Claude rejects a requested model."""


def _is_model_rejection_error(error_message: str) -> bool:
    lowered = (error_message or "").lower()
    patterns = (
        "invalid model",
        "unknown model",
        "model not found",
        "unsupported model",
        "not support model",
        "not a valid model",
    )
    return any(pattern in lowered for pattern in patterns)


def _resolve_opus_45_fallback(model_id: Optional[str]) -> Optional[str]:
    if not model_id or not model_id.startswith("claude-opus-4-6-"):
        return None

    opus_45_models = sorted(
        model.id
        for model in get_available_models()
        if model.id.startswith("claude-opus-4-5-")
    )
    if not opus_45_models:
        return None

    return opus_45_models[-1]


class ClaudeManager:
    """Manages multiple Claude Code processes."""

    def __init__(self):
        self.processes: Dict[str, ClaudeProcess] = {}
        self.cli_session_index: Dict[str, str] = {}
        self.max_concurrent = settings.max_concurrent_sessions
        self._session_lock = asyncio.Lock()

    async def get_version(self) -> str:
        """Get Claude Code version."""
        try:
            result = await asyncio.create_subprocess_exec(
                settings.claude_binary_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await result.communicate()

            if result.returncode == 0:
                version = stdout.decode().strip()
                return version
            error = stderr.decode().strip()
            raise ClaudeVersionError(f"Claude version check failed: {error}")

        except FileNotFoundError as exc:
            raise ClaudeBinaryNotFoundError(
                f"Claude binary not found at: {settings.claude_binary_path}"
            ) from exc
        except OSError as exc:
            raise ClaudeVersionError(
                f"Failed to get Claude version: {str(exc)}"
            ) from exc

    def _ensure_session_capacity(self, session_id: str) -> None:
        existing_process = self.processes.get(session_id)
        if existing_process and existing_process.is_running:
            raise ClaudeSessionConflictError(
                f"Session {session_id} already has an active Claude process"
            )
        if existing_process and not existing_process.is_running:
            self._cleanup_process(existing_process)

        if len(self.processes) >= self.max_concurrent:
            raise ClaudeConcurrencyError(
                f"Maximum concurrent sessions ({self.max_concurrent}) reached"
            )

    def _build_model_candidates(self, model: Optional[str]) -> List[Optional[str]]:
        candidates: List[Optional[str]] = [model]
        fallback_model = _resolve_opus_45_fallback(model)
        if fallback_model and fallback_model not in candidates:
            candidates.append(fallback_model)
        return candidates

    def _create_process(
        self,
        session_id: str,
        project_path: str,
        on_cli_session_id: Optional[Callable[[str], None]],
    ) -> ClaudeProcess:
        def _handle_cli_session_id(cli_session_id: str):
            self._register_cli_session(session_id, cli_session_id)
            if on_cli_session_id:
                on_cli_session_id(cli_session_id)

        return ClaudeProcess(
            session_id=session_id,
            project_path=project_path,
            on_cli_session_id=_handle_cli_session_id,
            on_end=self._cleanup_process,
        )

    def _raise_model_not_supported(
        self,
        selected_model: Optional[str],
        model_candidates: List[Optional[str]],
        last_error: str,
    ) -> None:
        available = ", ".join(model.id for model in get_available_models())
        raise ClaudeModelNotSupportedError(
            f"Claude rejected model '{selected_model or '<cli-default>'}'. "
            f"Attempted: {', '.join(str(m) for m in model_candidates)}. "
            f"Configured models: {available}. "
            f"Details: {last_error}"
        )

    async def _start_with_fallback_models(
        self,
        session_id: str,
        project_path: str,
        prompt: str,
        selected_model: Optional[str],
        system_prompt: Optional[str],
        on_cli_session_id: Optional[Callable[[str], None]],
        content: Optional[Any] = None,
    ) -> ClaudeProcess:
        model_candidates = self._build_model_candidates(selected_model)
        last_error = "Failed to start Claude process"

        for idx, candidate_model in enumerate(model_candidates):
            process = self._create_process(
                session_id=session_id,
                project_path=project_path,
                on_cli_session_id=on_cli_session_id,
            )
            success = await process.start(
                prompt=prompt,
                model=candidate_model,
                system_prompt=system_prompt,
                content=content,
            )

            if success:
                self.processes[session_id] = process
                if idx > 0:
                    logger.warning(
                        "Model fallback activated after rejection",
                        requested_model=selected_model or "<cli-default>",
                        fallback_model=candidate_model,
                        session_id=session_id,
                    )
                logger.info(
                    "Claude session created",
                    session_id=process.session_id,
                    active_sessions=len(self.processes),
                )
                return process

            last_error = process.last_error or last_error
            if not _is_model_rejection_error(last_error):
                raise ClaudeProcessStartError(last_error)

            has_next_candidate = idx + 1 < len(model_candidates)
            if has_next_candidate:
                logger.warning(
                    "Claude rejected model, retrying with fallback",
                    rejected_model=candidate_model,
                    fallback_model=model_candidates[idx + 1],
                    error=last_error,
                )
                continue

            self._raise_model_not_supported(
                selected_model=selected_model,
                model_candidates=model_candidates,
                last_error=last_error,
            )

        raise ClaudeProcessStartError(last_error)

    async def create_session(
        self,
        session_id: str,
        project_path: str,
        prompt: str,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        on_cli_session_id: Optional[Callable[[str], None]] = None,
        content: Optional[Any] = None,
    ) -> ClaudeProcess:
        """Create new Claude session."""
        async with self._session_lock:
            self._ensure_session_capacity(session_id)
            await asyncio.to_thread(os.makedirs, project_path, exist_ok=True)

            return await self._start_with_fallback_models(
                session_id=session_id,
                project_path=project_path,
                prompt=prompt,
                selected_model=model,
                system_prompt=system_prompt,
                on_cli_session_id=on_cli_session_id,
                content=content,
            )

    async def _stop_session_locked(self, session_id: str) -> None:
        resolved_id = self._resolve_session_id(session_id)
        if not resolved_id or resolved_id not in self.processes:
            return

        process = self.processes[resolved_id]
        await process.stop()
        self._cleanup_process(process)

        logger.info(
            "Claude session stopped",
            session_id=resolved_id,
            active_sessions=len(self.processes),
        )

    def get_session(self, session_id: str) -> Optional[ClaudeProcess]:
        """Get existing Claude session."""
        resolved_id = self._resolve_session_id(session_id)
        if not resolved_id:
            return None
        return self.processes.get(resolved_id)

    async def stop_session(self, session_id: str):
        """Stop Claude session."""
        async with self._session_lock:
            await self._stop_session_locked(session_id)

    async def cleanup_all(self):
        """Stop all Claude sessions."""
        async with self._session_lock:
            for session_id in tuple(self.processes):
                await self._stop_session_locked(session_id)

        logger.info("All Claude sessions cleaned up")

    def get_active_sessions(self) -> List[str]:
        """Get list of active session IDs."""
        return list(self.processes.keys())

    async def continue_conversation(self, session_id: str, prompt: str) -> bool:
        """Continue existing conversation."""
        resolved_id = self._resolve_session_id(session_id)
        if not resolved_id:
            return False
        process = self.processes.get(resolved_id)
        if not process:
            return False

        await process.send_input(prompt)
        return True

    def _register_cli_session(self, api_session_id: str, cli_session_id: str):
        if not cli_session_id:
            return
        self.cli_session_index[cli_session_id] = api_session_id

    def _resolve_session_id(self, session_id: str) -> Optional[str]:
        if session_id in self.processes:
            return session_id
        return self.cli_session_index.get(session_id)

    def _cleanup_process(self, process: ClaudeProcess):
        api_session_id = process.session_id
        if api_session_id in self.processes:
            del self.processes[api_session_id]
        if process.cli_session_id:
            self.cli_session_index.pop(process.cli_session_id, None)


# Utility functions for project management
def create_project_directory(project_id: str) -> str:
    """Create project directory."""
    return ensure_directory_within_base(
        project_id,
        settings.project_root,
        allow_subpaths=False,
        sanitize_leaf=True,
    )


def cleanup_project_directory(project_path: str):
    """Clean up project directory."""
    try:
        import shutil

        if os.path.exists(project_path):
            shutil.rmtree(project_path)
            logger.info("Project directory cleaned up", path=project_path)
    except Exception as e:
        logger.error(
            "Failed to cleanup project directory", path=project_path, error=str(e)
        )


def validate_claude_binary() -> bool:
    """Validate Claude binary availability."""
    try:
        result = subprocess.run(
            [settings.claude_binary_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False
