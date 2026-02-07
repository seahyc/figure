from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


RUNS_ROOT = Path(__file__).resolve().parents[2] / "runs"


@dataclass
class RunConfig:
    agent: str
    url: str
    started_at: str
    llm_mode: str
    headless: bool
    channel: str


class RunLogger:
    def __init__(self, *, agent: str, url: str, llm_mode: str, headless: bool, channel: str) -> None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_id = f"{ts}-{agent}"
        self.run_dir = RUNS_ROOT / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

        (self.run_dir / "screenshots").mkdir(exist_ok=True)
        (self.run_dir / "artifacts").mkdir(exist_ok=True)

        cfg = RunConfig(
            agent=agent,
            url=url,
            started_at=datetime.now().isoformat(),
            llm_mode=llm_mode,
            headless=headless,
            channel=channel,
        )
        (self.run_dir / "config.json").write_text(json.dumps(asdict(cfg), indent=2))

    async def screenshot(self, page, *, step: int, label: str) -> None:
        path = self.run_dir / "screenshots" / f"step-{step:03d}-{label}.png"
        try:
            data = await page.screenshot(type="png", full_page=True)
            path.write_bytes(data)
        except Exception:
            # Screenshot failures shouldn't break runs.
            pass

    def write_json(self, name: str, payload: Any) -> None:
        path = self.run_dir / "artifacts" / name
        path.write_text(json.dumps(payload, indent=2, default=str))

