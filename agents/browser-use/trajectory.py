"""
Trajectory: Structured logging of agent episodes for offline RL.

Each trajectory is a sequence of (observation, action, result, reward) steps.
Saved as JSONL — one line per step, streamable.
"""

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class TrajectoryStep:
    """A single step in an agent trajectory."""
    step_num: int
    timestamp: float
    observation: str  # observation.to_prompt() text
    action_type: str
    action_params: dict
    thought: str = ""
    result: dict = field(default_factory=dict)
    reward: float = 0.0
    plan_time_ms: float = 0.0
    exec_time_ms: float = 0.0


@dataclass
class Trajectory:
    """A complete agent episode."""
    task_id: str
    task_description: str
    url: str
    steps: list[TrajectoryStep] = field(default_factory=list)
    total_reward: float = 0.0
    success: bool = False
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    metadata: dict = field(default_factory=dict)

    def add_step(self, step: TrajectoryStep):
        self.steps.append(step)
        self.total_reward += step.reward

    def finish(self, success: bool = False):
        self.success = success
        self.end_time = time.time()
        # Recompute total reward
        self.total_reward = sum(s.reward for s in self.steps)

    @property
    def duration_seconds(self) -> float:
        end = self.end_time or time.time()
        return end - self.start_time

    def save_jsonl(self, path: Path):
        """Save trajectory as JSONL (one line per step, plus header)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            # Header line
            header = {
                "type": "trajectory_header",
                "task_id": self.task_id,
                "task_description": self.task_description[:200],
                "url": self.url,
                "total_reward": self.total_reward,
                "success": self.success,
                "duration_s": round(self.duration_seconds, 1),
                "num_steps": len(self.steps),
                "metadata": self.metadata,
            }
            f.write(json.dumps(header, default=str) + "\n")
            # Step lines
            for step in self.steps:
                line = {
                    "type": "step",
                    "step_num": step.step_num,
                    "timestamp": step.timestamp,
                    "observation": step.observation[:500],  # truncate for storage
                    "action_type": step.action_type,
                    "action_params": step.action_params,
                    "thought": step.thought,
                    "result_ok": step.result.get("ok", False),
                    "result_detail": step.result.get("detail", "")[:200],
                    "reward": step.reward,
                    "plan_time_ms": round(step.plan_time_ms, 1),
                    "exec_time_ms": round(step.exec_time_ms, 1),
                }
                f.write(json.dumps(line, default=str) + "\n")

    @classmethod
    def load_jsonl(cls, path: Path) -> "Trajectory":
        """Load trajectory from JSONL file."""
        with open(path) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        if not lines:
            raise ValueError(f"Empty trajectory file: {path}")

        header = lines[0]
        traj = cls(
            task_id=header.get("task_id", ""),
            task_description=header.get("task_description", ""),
            url=header.get("url", ""),
            total_reward=header.get("total_reward", 0.0),
            success=header.get("success", False),
            metadata=header.get("metadata", {}),
        )
        for line in lines[1:]:
            if line.get("type") != "step":
                continue
            step = TrajectoryStep(
                step_num=line.get("step_num", 0),
                timestamp=line.get("timestamp", 0),
                observation=line.get("observation", ""),
                action_type=line.get("action_type", ""),
                action_params=line.get("action_params", {}),
                thought=line.get("thought", ""),
                result={"ok": line.get("result_ok", False), "detail": line.get("result_detail", "")},
                reward=line.get("reward", 0.0),
                plan_time_ms=line.get("plan_time_ms", 0),
                exec_time_ms=line.get("exec_time_ms", 0),
            )
            traj.steps.append(step)
        return traj
