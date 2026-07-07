"""
REINFORCE training loop.

Collects full episodes, computes discounted returns, and updates the policy
with a Monte Carlo policy gradient step. Checkpoints are saved to
experiments/results/<run_id>/ every save_every episodes.

Usage:
    python scripts/train.py experiments/configs/s002-train-001.yml
    python scripts/train.py experiments/configs/s002-train-001.yml --log-file custom.log
"""

import argparse
import csv
import json
import logging
import os
import random
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import torch
import yaml
from dotenv import load_dotenv

from rl.actions import Action
from rl.environment import Environment, EnvironmentConfig
from rl.logging_setup import setup_logging
from rl.policy import Policy

load_dotenv()

logger = logging.getLogger("rl.train")

# ── Argument parsing ──────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="REINFORCE training loop.")
parser.add_argument("config", type=Path, help="Path to YAML training config")
parser.add_argument("--log-file", default=None, help="Log file path (default: results_dir/<run_id>/train.log)")
parser.add_argument("--resume", type=Path, default=None, help="Path to checkpoint .pt file to resume from")
args = parser.parse_args()

# ── Config ────────────────────────────────────────────────────────────────────

with open(args.config) as f:
    raw = yaml.safe_load(f)

# ── Preflight checks ─────────────────────────────────────────────────────────

if not os.environ.get("DEEPINFRA_API_KEY"):
    raise RuntimeError("DEEPINFRA_API_KEY is not set. Add it to .env before training.")

# ── Seeds ─────────────────────────────────────────────────────────────────────

seed_python = raw.get("seed_python", 42)
seed_torch = raw.get("seed_torch", 42)
random.seed(seed_python)
torch.manual_seed(seed_torch)

# ── Results dir ───────────────────────────────────────────────────────────────

run_id = args.config.stem
results_dir = Path(raw.get("results_dir", "experiments/results")) / run_id
results_dir.mkdir(parents=True, exist_ok=True)

log_file = args.log_file or str(results_dir / "train.log")
setup_logging(log_file)

# ── Environment / Policy / Optimizer ─────────────────────────────────────────

env_config = EnvironmentConfig(
    container_name=raw["container_name"],
    max_steps=raw.get("max_steps", 40),
    dry_run=raw.get("dry_run", False),
    timeout=raw.get("timeout", 60),
    max_output_chars=raw.get("max_output_chars", 4000),
    model=raw.get("model", "moonshotai/Kimi-K2.6"),
    context_window=raw.get("context_window", 3),
    api_timeout=raw.get("api_timeout", 60),
    reasoning_effort=raw.get("reasoning_effort", None),
)
env = Environment(env_config)

policy = Policy(
    hidden_dim=raw.get("hidden_dim", 128),
    num_layers=raw.get("num_layers", 2),
    conditioned_action_head=raw.get("conditioned_action_head", False),
    duration_options=raw.get("duration_options"),
)

# A duration block longer than context_window would lose visibility into its own
# earlier tries mid-block (see ADR 011) — fail at startup rather than silently
# wasting a training run.
if env_config.context_window < max(policy.duration_options):
    raise ValueError(
        f"context_window ({env_config.context_window}) must be >= the largest "
        f"duration_options value ({max(policy.duration_options)}), or a multi-try "
        "block can be pruned out of its own context before it finishes. See ADR 011."
    )

optimizer = torch.optim.Adam(policy.parameters(), lr=raw.get("learning_rate", 1e-3))

resume_episode = 0
if args.resume:
    checkpoint = torch.load(args.resume, weights_only=True)
    policy.load_state_dict(checkpoint["policy_state_dict"])
    optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
    resume_episode = checkpoint["episode"]
    logger.info("Resumed from checkpoint: %s (episode %d)", args.resume, resume_episode)

# ── Hyperparameters ───────────────────────────────────────────────────────────

num_episodes = raw["num_episodes"]
gamma = raw.get("gamma", 0.99)
use_baseline = raw.get("use_baseline", True)
save_every = raw.get("save_every", 10)
grad_clip = raw.get("grad_clip", 1.0)
entropy_coeff = raw.get("entropy_coeff", 0.0)

# ── Tracking setup ────────────────────────────────────────────────────────────

def _git_commit() -> str:
    try:
        hash_ = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        return hash_ + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


metadata = {
    "run_id": run_id,
    "git_commit": _git_commit(),
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "config": raw,
}
with open(results_dir / "run_metadata.json", "w") as f:
    json.dump(metadata, f, indent=2)
logger.info("Run metadata written: commit=%s", metadata["git_commit"])

_ACTION_COLS = [f"act_{a.name.lower()}" for a in Action]
_TRIES_COLS = [f"tries_{a.name.lower()}" for a in Action]
_CSV_FIELDS = ["episode", "total_reward", "loss", "exploit_count", "elapsed_s", "entropy"] + _ACTION_COLS + _TRIES_COLS

_csv_mode = "a" if (args.resume and (results_dir / "rewards.csv").exists()) else "w"
_rewards_csv = open(results_dir / "rewards.csv", _csv_mode, newline="")
_csv_writer = csv.DictWriter(_rewards_csv, fieldnames=_CSV_FIELDS)
if _csv_mode == "w":
    _csv_writer.writeheader()
_rewards_csv.flush()

_STEPS_FIELDS = ["episode", "step", "action", "reward", "tries_used"]
_steps_csv = open(results_dir / "steps.csv", _csv_mode, newline="")
_steps_writer = csv.DictWriter(_steps_csv, fieldnames=_STEPS_FIELDS)
if _csv_mode == "w":
    _steps_writer.writeheader()
_steps_csv.flush()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_returns(rewards: list[float], gamma: float) -> torch.Tensor:
    G = 0.0
    returns: list[float] = []
    for r in reversed(rewards):
        G = r + gamma * G
        returns.append(G)
    returns.reverse()
    return torch.tensor(returns, dtype=torch.float32)


def _save_checkpoint(episode: int) -> None:
    path = results_dir / f"checkpoint_ep{episode:04d}.pt"
    torch.save({
        "episode": episode,
        "policy_state_dict": policy.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "config": raw,
    }, path)
    logger.info("Checkpoint saved: %s", path)


# ── Startup summary ───────────────────────────────────────────────────────────

print(f"Config:      {args.config}")
print(f"Run ID:      {run_id}")
print(f"Container:   {env_config.container_name}")
print(f"Episodes:    {num_episodes}  steps/ep={env_config.max_steps}")
print(f"Model:       {env_config.model}")
print(f"Policy:      hidden_dim={raw.get('hidden_dim', 128)}  num_layers={raw.get('num_layers', 2)}")
print(f"Duration:    options={policy.duration_options}  context_window={env_config.context_window}")
print(f"γ={gamma}  lr={raw.get('learning_rate', 1e-3)}  baseline={use_baseline}  entropy_coeff={entropy_coeff}")
print(f"Seeds:       python={seed_python}  torch={seed_torch}")
print(f"Resume:      {args.resume or 'no'}  (starting at episode {resume_episode + 1})")
print(f"Results:     {results_dir}")
print(f"Log:         {log_file}")
print(f"Debug log:   {log_file.replace('.log', '.debug.log')}")
print()

# ── Training loop ─────────────────────────────────────────────────────────────

episode_rewards: list[float] = []
run_start = time.time()

for episode in range(resume_episode + 1, resume_episode + num_episodes + 1):
    ep_start = time.time()
    state = env.reset()

    log_probs: list[torch.Tensor] = []
    rewards: list[float] = []
    exploits: list[str] = []
    entropies: list[float] = []
    action_counts: Counter = Counter()
    tries_counts: Counter = Counter()

    done = False
    while not done:
        known_host_count = len(env._state.known_hosts())
        action, host_slot, duration, log_prob, entropy = policy.sample(state, known_host_count)

        # host_slot 0 (no_host) and 1 (all_hosts) are broadcast — host_idx is ignored
        host_idx = max(0, host_slot - 2)

        # One block (up to `duration` consecutive tries of the same action/host) is
        # one decision from the policy's perspective — it gets exactly one log_prob
        # and one aggregated reward below, whatever its real try count turned out to be.
        state, reward, done, info = env.step_block(action, host_idx, duration)

        skip = info.get("skip")
        if not skip:
            log_probs.append(log_prob)
            rewards.append(reward)
            entropies.append(entropy.item())
            action_counts[action] += 1
            tries_counts[action] += info["tries_used"]
            if info.get("exploit"):
                exploits.append(f"{info['host']} ({info['exploit'].vulnerability})")

        action_label = action.name if not skip else skip.upper()
        _steps_writer.writerow({
            "episode": episode, "step": info["step"], "action": action_label,
            "reward": reward, "tries_used": info["tries_used"],
        })

    # Discounted returns
    returns = _compute_returns(rewards, gamma)
    if use_baseline:
        returns = returns - returns.mean()

    # Policy gradient update
    entropy_bonus = torch.tensor(entropies).sum() if entropy_coeff > 0.0 else torch.tensor(0.0)
    loss = -(torch.stack(log_probs) * returns).sum() - entropy_coeff * entropy_bonus
    optimizer.zero_grad()
    loss.backward()
    torch.nn.utils.clip_grad_norm_(policy.parameters(), max_norm=grad_clip)
    if torch.isnan(loss):
        raise RuntimeError(
            f"Loss is NaN at episode {episode}. "
            "Check for exploding gradients or degenerate log-probs."
        )
    optimizer.step()

    total_reward = sum(rewards)
    episode_rewards.append(total_reward)
    ep_elapsed = time.time() - ep_start
    mean_entropy = sum(entropies) / len(entropies) if entropies else 0.0

    logger.info(
        "Episode %d/%d  reward=%+.1f  exploits=%d  loss=%.4f  entropy=%.3f  elapsed=%.1fs",
        episode, num_episodes, total_reward, len(exploits), loss.item(), mean_entropy, ep_elapsed,
    )
    print(
        f"Ep {episode:4d}/{num_episodes}  reward={total_reward:+6.1f}  "
        f"exploits={len(exploits)}  loss={loss.item():8.4f}  entropy={mean_entropy:.3f}  ({ep_elapsed:.0f}s)"
    )

    _csv_writer.writerow({
        "episode": episode,
        "total_reward": round(total_reward, 2),
        "loss": round(loss.item(), 6),
        "exploit_count": len(exploits),
        "elapsed_s": round(ep_elapsed, 1),
        "entropy": round(mean_entropy, 4),
        **{f"act_{a.name.lower()}": action_counts.get(a, 0) for a in Action},
        **{f"tries_{a.name.lower()}": tries_counts.get(a, 0) for a in Action},
    })
    _rewards_csv.flush()
    _steps_csv.flush()

    if episode % save_every == 0 or episode == num_episodes:
        _save_checkpoint(episode)

# ── Final summary ─────────────────────────────────────────────────────────────

_rewards_csv.close()
_steps_csv.close()
total_elapsed = time.time() - run_start
mean_reward = sum(episode_rewards) / len(episode_rewards)
print()
print("─" * 60)
print(f"Episodes:      {num_episodes}")
print(f"Mean reward:   {mean_reward:+.2f}")
print(f"Best reward:   {max(episode_rewards):+.1f}")
print(f"Total time:    {total_elapsed:.1f}s  ({total_elapsed / num_episodes:.1f}s/ep)")
print(f"Results:       {results_dir}")
print(f"Log:           {log_file}")
print(f"Debug log:     {log_file.replace('.log', '.debug.log')}")
