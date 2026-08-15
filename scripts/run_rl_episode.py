"""
Run a single RL episode with a random policy.

Used for smoke-testing the RL environment against a live sandbox before
implementing the policy network. The random policy samples uniformly from
the set of currently valid (action, host_idx) pairs each step.

Usage:
    python scripts/run_rl_episode.py experiments/configs/s002-rl-001.yml
    python scripts/run_rl_episode.py experiments/configs/s002-rl-001.yml --log-file custom.log
"""

import argparse
import random
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

from rl.actions import Action, BROADCAST_ACTIONS, is_valid
from rl.environment import Environment, EnvironmentConfig
from rl.logging_setup import setup_logging

load_dotenv()

parser = argparse.ArgumentParser(description="Run a single RL episode with a random policy.")
parser.add_argument("config", type=Path, help="Path to YAML episode config")
parser.add_argument("--log-file", default="run.log", help="Detailed log output (default: run.log)")
args = parser.parse_args()

with open(args.config) as f:
    raw = yaml.safe_load(f)

seed = raw.get("seed", 42)
random.seed(seed)

setup_logging(args.log_file)

config = EnvironmentConfig(
    container_name=raw["container_name"],
    max_steps=raw.get("max_steps", 40),
    dry_run=raw.get("dry_run", False),
    timeout=raw.get("timeout", 60),
    max_output_chars=raw.get("max_output_chars", 4000),
    model=raw.get("model", "moonshotai/Kimi-K2.6"),
    base_url=raw.get("base_url", "https://api.deepinfra.com/v1/openai"),
    api_key_env=raw.get("api_key_env", "DEEPINFRA_API_KEY"),
)

print(f"Config:    {args.config}")
print(f"Container: {config.container_name}")
print(f"Steps:     {config.max_steps}")
print(f"Model:     {config.model}")
print(f"Seed:      {seed}")
print(f"Log file:  {args.log_file}")
print()


def _random_action(env: Environment) -> tuple[Action, int]:
    """Sample a valid (action, host_idx) pair uniformly at random."""
    hosts = env._state.known_hosts()
    candidates: list[tuple[Action, int]] = []

    for action in BROADCAST_ACTIONS - {Action.DO_NOTHING}:
        candidates.append((action, 0))

    for i, ip in enumerate(hosts):
        features = env._state.host_features(ip)
        for action in Action:
            if action not in BROADCAST_ACTIONS and is_valid(action, features):
                candidates.append((action, i))

    return random.choice(candidates)


env = Environment(config)
env.reset()

total_reward = 0.0
exploits: list[str] = []
start = time.time()

done = False
while not done:
    action, host_idx = _random_action(env)
    _, reward, done, info = env.step(action, host_idx)
    total_reward += reward
    if info.get("exploit"):
        exploits.append(f"{info['host']} ({info['exploit'].vulnerability})")

elapsed = time.time() - start
print()
print("─" * 52)
print(f"Steps:         {env.step_count}/{config.max_steps}")
print(f"Total reward:  {total_reward:+.1f}")
print(f"Exploits ({len(exploits)}):  {', '.join(exploits) or 'none'}")
print(f"Elapsed:       {elapsed:.1f}s")
print(f"Log:           {args.log_file}")
