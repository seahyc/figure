"""
Reward functions for browser agent trajectories.

Three reward types:
- BinaryTaskReward: +1 when agent calls done(success=True), 0 otherwise
- ProgressReward: +1 for each regex match increase (e.g., Step N tracking)
- LLMJudgeReward: uses Gemini Flash to judge answer quality
"""

import json
import os
import re
from abc import ABC, abstractmethod


class RewardFunction(ABC):
    """Base class for reward functions."""

    @abstractmethod
    async def compute(self, page, action_type: str, action_result: dict) -> float:
        """Compute reward for a single step.

        Args:
            page: Browser page object (for reading page state)
            action_type: The action that was just executed
            action_result: Result dict from the executor

        Returns:
            Reward value (typically 0.0 or 1.0)
        """
        ...


class BinaryTaskReward(RewardFunction):
    """Simple binary reward: +1 when agent signals done with success."""

    async def compute(self, page, action_type: str, action_result: dict) -> float:
        if action_type == "done" and action_result.get("success"):
            return 1.0
        return 0.0


class ProgressReward(RewardFunction):
    """Reward based on progress counter matching a regex pattern.

    Example: pattern=r"Step\\s+(\\d+)" rewards +1 each time the step number increases.
    """

    def __init__(self, pattern: str, max_value: int = 30):
        self.pattern = re.compile(pattern)
        self.max_value = max_value
        self._last_value = 0

    async def compute(self, page, action_type: str, action_result: dict) -> float:
        try:
            text = await page.evaluate(
                "() => document.body ? document.body.innerText.substring(0, 3000) : ''"
            )
            match = self.pattern.search(text)
            if match:
                current = int(match.group(1))
                if current > self._last_value:
                    reward = float(current - self._last_value)
                    self._last_value = current
                    return reward
        except Exception:
            pass
        return 0.0

    @property
    def current_progress(self) -> int:
        return self._last_value


class LLMJudgeReward(RewardFunction):
    """Use Gemini Flash to judge if the agent's answer matches expected criteria.

    Best for information extraction tasks where exact match isn't possible.
    """

    def __init__(self, task_description: str, expected_criteria: str = ""):
        self.task_description = task_description
        self.expected_criteria = expected_criteria
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google import genai
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GOOGLE_API_KEY required for LLM judge")
            self._client = genai.Client(api_key=api_key)
        return self._client

    async def compute(self, page, action_type: str, action_result: dict) -> float:
        if action_type != "done":
            return 0.0

        summary = action_result.get("detail", "")
        if not summary:
            return 0.0

        try:
            from google.genai import types
            client = self._get_client()

            prompt = f"""Task: {self.task_description}

Agent's answer: {summary}

{f'Expected criteria: {self.expected_criteria}' if self.expected_criteria else ''}

Rate the agent's answer on a scale of 0.0 to 1.0:
- 1.0: Fully correct and complete answer
- 0.5: Partially correct or incomplete
- 0.0: Wrong or irrelevant

Respond with a JSON object: {{"score": <float>, "reason": "<brief explanation>"}}"""

            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                    max_output_tokens=100,
                ),
            )
            result = json.loads(response.text)
            return float(result.get("score", 0.0))
        except Exception as e:
            print(f"    [judge] Error: {e}")
            return 0.0


def create_reward(reward_type: str, **kwargs) -> RewardFunction:
    """Factory function to create reward functions from config."""
    if reward_type == "binary":
        return BinaryTaskReward()
    elif reward_type == "progress":
        pattern = kwargs.get("pattern", r"Step\s+(\d+)")
        max_value = kwargs.get("max_value", 30)
        return ProgressReward(pattern=pattern, max_value=max_value)
    elif reward_type == "llm_judge":
        task = kwargs.get("task_description", "")
        criteria = kwargs.get("expected_criteria", "")
        return LLMJudgeReward(task_description=task, expected_criteria=criteria)
    else:
        raise ValueError(f"Unknown reward type: {reward_type}")
