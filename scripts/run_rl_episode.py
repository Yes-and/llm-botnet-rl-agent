"""
Run a single RL episode with a random policy.

Used for smoke-testing the RL environment against a live sandbox before
implementing the policy network. Host selection is a uniform random pick from
the pool each engagement (ADR 014 Phase 1: the selector isn't learned yet);
within an engagement, the action is a uniform random pick among currently
valid actions for the active host (rl.actions.is_valid()).

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

from rl.actions import Action, is_valid
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
    max_engagement_steps=raw.get("max_engagement_steps", 10),
    dry_run=raw.get("dry_run", False),
    timeout=raw.get("timeout", 60),
    max_output_chars=raw.get("max_output_chars", 4000),
    model=raw.get("model", "moonshotai/Kimi-K2.6"),
)

print(f"Config:    {args.config}")
print(f"Container: {config.container_name}")
print(f"Steps:     {config.max_steps}")
print(f"Model:     {config.model}")
print(f"Seed:      {seed}")
print(f"Full space: {raw.get('full_action_space', False)}")
print(f"Log file:  {args.log_file}")
print()


def _random_action(env: Environment, host_ip: str, full_action_space: bool) -> Action:
    """Sample a valid action uniformly at random for the active host."""
    features = env._state.host_features(host_ip)
    candidates = [
        a for a in Action
        if is_valid(a, features, env.engagement_step_count, full_action_space)
    ]
    return random.choice(candidates)


env = Environment(config)
env.reset()

total_reward = 0.0
exploits: list[str] = []
start = time.time()

done = False
while not done:
    hosts = env._state.known_hosts()
    if not hosts:
        break  # pool exhausted — nothing left to engage
    host_ip = random.choice(hosts)  # Phase 1: selector isn't learned, pick uniformly at random
    env.start_engagement(host_ip)

    engagement_done = False
    while not engagement_done and not done:
        action = _random_action(env, host_ip, raw.get("full_action_space", False))
        _, reward, done, info = env.interact(action)
        total_reward += reward
        engagement_done = info["engagement_done"]
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
