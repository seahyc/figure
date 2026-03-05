"""
Build DPO preference pairs from collected trajectories.

Groups trajectories by task, ranks by total reward, and builds
(chosen, rejected) pairs for DPO training.

Usage:
    python training/build_pairs.py --trajectories runs/trajectories/ --output training/pairs.json
"""

import json
import argparse
import sys
from pathlib import Path
from collections import defaultdict

# Ensure parent dir is on path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from trajectory import Trajectory


def load_trajectories(traj_dir: Path) -> list[Trajectory]:
    """Load all trajectory JSONL files from a directory tree."""
    trajectories = []
    for jsonl_path in sorted(traj_dir.rglob("*.jsonl")):
        try:
            traj = Trajectory.load_jsonl(jsonl_path)
            trajectories.append(traj)
        except Exception as e:
            print(f"Warning: failed to load {jsonl_path}: {e}")
    return trajectories


def build_preference_pairs(trajectories: list[Trajectory], min_reward_diff: float = 0.5) -> list[dict]:
    """Build DPO preference pairs from trajectory comparisons.

    For each task, compare trajectories and create pairs where:
    - chosen: action from the higher-reward trajectory
    - rejected: action from the lower-reward trajectory
    at the same step (same observation context).

    Args:
        trajectories: List of Trajectory objects
        min_reward_diff: Minimum reward difference to form a pair

    Returns:
        List of {prompt, chosen, rejected} dicts
    """
    # Group by task
    by_task: dict[str, list[Trajectory]] = defaultdict(list)
    for traj in trajectories:
        by_task[traj.task_id].append(traj)

    pairs = []

    for task_id, task_trajs in by_task.items():
        if len(task_trajs) < 2:
            continue

        # Sort by total reward (descending)
        task_trajs.sort(key=lambda t: t.total_reward, reverse=True)

        # Compare best vs others
        for i, better in enumerate(task_trajs):
            for worse in task_trajs[i + 1:]:
                if better.total_reward - worse.total_reward < min_reward_diff:
                    continue

                # Align steps and create pairs
                max_steps = min(len(better.steps), len(worse.steps))
                for s in range(max_steps):
                    bs = better.steps[s]
                    ws = worse.steps[s]

                    # Only pair steps where actions differ
                    if bs.action_type == ws.action_type and bs.action_params == ws.action_params:
                        continue

                    # Use observation as prompt context
                    prompt = f"TASK: {better.task_description[:200]}\n\nOBSERVATION:\n{bs.observation[:400]}"
                    chosen = json.dumps({
                        "thought": bs.thought,
                        "action_type": bs.action_type,
                        "params": bs.action_params,
                    })
                    rejected = json.dumps({
                        "thought": ws.thought,
                        "action_type": ws.action_type,
                        "params": ws.action_params,
                    })

                    pairs.append({
                        "prompt": prompt,
                        "chosen": chosen,
                        "rejected": rejected,
                        "task_id": task_id,
                        "step": s,
                        "reward_diff": better.total_reward - worse.total_reward,
                    })

    return pairs


def main():
    parser = argparse.ArgumentParser(description="Build DPO preference pairs from trajectories")
    parser.add_argument("--trajectories", "-t", required=True,
                        help="Directory containing trajectory JSONL files")
    parser.add_argument("--output", "-o", default="training/pairs.json",
                        help="Output path for preference pairs (default: training/pairs.json)")
    parser.add_argument("--min-reward-diff", type=float, default=0.5,
                        help="Minimum reward difference for a valid pair (default: 0.5)")
    parser.add_argument("--format", choices=["json", "jsonl", "huggingface"], default="json",
                        help="Output format (default: json)")
    args = parser.parse_args()

    traj_dir = Path(args.trajectories)
    if not traj_dir.exists():
        print(f"Error: trajectory directory not found: {traj_dir}")
        return

    print(f"Loading trajectories from {traj_dir}...")
    trajectories = load_trajectories(traj_dir)
    print(f"Loaded {len(trajectories)} trajectories")

    # Stats
    by_task = defaultdict(list)
    for t in trajectories:
        by_task[t.task_id].append(t)
    for task_id, trajs in sorted(by_task.items()):
        rewards = [t.total_reward for t in trajs]
        print(f"  {task_id}: {len(trajs)} runs, rewards={[round(r, 1) for r in rewards]}")

    print(f"\nBuilding preference pairs (min_reward_diff={args.min_reward_diff})...")
    pairs = build_preference_pairs(trajectories, min_reward_diff=args.min_reward_diff)
    print(f"Generated {len(pairs)} preference pairs")

    # Save
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.format == "jsonl":
        with open(output_path, "w") as f:
            for pair in pairs:
                f.write(json.dumps(pair) + "\n")
    elif args.format == "huggingface":
        # HuggingFace datasets format
        hf_pairs = [{"prompt": p["prompt"], "chosen": p["chosen"], "rejected": p["rejected"]}
                     for p in pairs]
        with open(output_path, "w") as f:
            json.dump(hf_pairs, f, indent=2)
    else:
        with open(output_path, "w") as f:
            json.dump(pairs, f, indent=2)

    print(f"Saved to {output_path}")

    # Summary by task
    from collections import Counter
    task_counts = Counter(p["task_id"] for p in pairs)
    for task_id, count in task_counts.most_common():
        print(f"  {task_id}: {count} pairs")


if __name__ == "__main__":
    main()
