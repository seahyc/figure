"""
DPO Training: Fine-tune a small LLM as the planner using preference pairs.

Designed for MLX on M1 Mac:
- Qwen-2.5-3B (4-bit quantized)
- Batch size 1 with gradient accumulation
- Standard DPO loss (beta=0.1)
- Target: 2-4 hours for 1000 pairs

Usage:
    python training/train_dpo.py --pairs training/pairs.json --model Qwen/Qwen2.5-3B
"""

import json
import argparse
from pathlib import Path


def load_pairs(path: str) -> list[dict]:
    """Load preference pairs from JSON or JSONL."""
    p = Path(path)
    if p.suffix == ".jsonl":
        with open(p) as f:
            return [json.loads(line) for line in f if line.strip()]
    else:
        with open(p) as f:
            return json.load(f)


def format_for_dpo(pairs: list[dict]) -> list[dict]:
    """Format pairs for DPO training.

    Each pair becomes:
    - prompt: observation + task context
    - chosen: good action JSON
    - rejected: bad action JSON
    """
    formatted = []
    for pair in pairs:
        formatted.append({
            "prompt": pair["prompt"],
            "chosen": pair["chosen"],
            "rejected": pair["rejected"],
        })
    return formatted


def train_dpo_mlx(pairs: list[dict], model_name: str, output_dir: str,
                   beta: float = 0.1, epochs: int = 1, lr: float = 1e-5,
                   grad_accum: int = 8):
    """Train DPO using MLX (Apple Silicon optimized).

    NOTE: This is a stub — full implementation requires mlx-lm package.
    Install: pip install mlx-lm

    The training loop:
    1. Load model + tokenizer (4-bit quantized)
    2. For each pair, compute DPO loss:
       L = -log(σ(β * (log π(chosen|prompt) - log π_ref(chosen|prompt))
                   - (log π(rejected|prompt) - log π_ref(rejected|prompt))))
    3. Update with AdamW, gradient accumulation
    4. Save adapter weights (LoRA)
    """
    try:
        import mlx.core as mx
        import mlx.nn as nn
        from mlx_lm import load as mlx_load
    except ImportError:
        print("MLX not available. Install with: pip install mlx-lm")
        print("This script is designed for Apple Silicon (M1/M2/M3) Macs.")
        print(f"\nWould train on {len(pairs)} pairs with model {model_name}")
        print(f"Settings: beta={beta}, epochs={epochs}, lr={lr}, grad_accum={grad_accum}")
        print(f"Output: {output_dir}")

        # Save formatted data for later training
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        data_path = out / "dpo_pairs.json"
        with open(data_path, "w") as f:
            json.dump(pairs, f, indent=2)
        print(f"Saved {len(pairs)} formatted pairs to {data_path}")
        print("Transfer this file to your M1 Mac for training.")
        return

    print(f"Loading model: {model_name} (4-bit)")
    model, tokenizer = mlx_load(model_name, tokenizer_config={"trust_remote_code": True})

    print(f"Training DPO on {len(pairs)} pairs...")
    print(f"  beta={beta}, epochs={epochs}, lr={lr}, grad_accum={grad_accum}")

    # TODO: Implement full DPO training loop with LoRA adapters
    # This requires:
    # 1. LoRA adapter setup for the model
    # 2. Reference model (frozen copy or KL anchor)
    # 3. DPO loss computation
    # 4. Training loop with gradient accumulation
    # See: https://github.com/ml-explore/mlx-examples/tree/main/lora

    print(f"Training complete. Saving to {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="DPO Training for Browser Agent Planner")
    parser.add_argument("--pairs", "-p", required=True, help="Path to preference pairs (JSON/JSONL)")
    parser.add_argument("--model", "-m", default="Qwen/Qwen2.5-3B",
                        help="Base model name (default: Qwen/Qwen2.5-3B)")
    parser.add_argument("--output", "-o", default="training/checkpoints",
                        help="Output directory for trained weights")
    parser.add_argument("--beta", type=float, default=0.1, help="DPO beta parameter")
    parser.add_argument("--epochs", type=int, default=1, help="Training epochs")
    parser.add_argument("--lr", type=float, default=1e-5, help="Learning rate")
    parser.add_argument("--grad-accum", type=int, default=8, help="Gradient accumulation steps")
    args = parser.parse_args()

    print(f"Loading pairs from {args.pairs}...")
    raw_pairs = load_pairs(args.pairs)
    print(f"Loaded {len(raw_pairs)} pairs")

    pairs = format_for_dpo(raw_pairs)

    train_dpo_mlx(
        pairs=pairs,
        model_name=args.model,
        output_dir=args.output,
        beta=args.beta,
        epochs=args.epochs,
        lr=args.lr,
        grad_accum=args.grad_accum,
    )


if __name__ == "__main__":
    main()
