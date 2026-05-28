import argparse
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

from agent.loop import EpisodeConfig, StepRecord, run_episode

load_dotenv()

parser = argparse.ArgumentParser(description="Run a single agent episode.")
parser.add_argument("config", type=Path, help="Path to YAML episode config")
parser.add_argument("--dry-run", action="store_true", help="Skip LLM calls and executor; print commands only")
args = parser.parse_args()

with open(args.config) as f:
    raw = yaml.safe_load(f)

config = EpisodeConfig(
    task=raw["task"],
    container_name=raw["container_name"],
    max_steps=raw.get("max_steps", 10),
    dry_run=args.dry_run,
    timeout=raw.get("timeout", 60),
    max_output_chars=raw.get("max_output_chars", 4000),
    model=raw.get("model", "moonshotai/Kimi-K2.6"),
)

print(f"Config:    {args.config}")
print(f"Task:      {config.task}")
print(f"Container: {config.container_name}")
print(f"Steps:     {config.max_steps}")
print(f"Model:     {config.model}")
print(f"Dry run:   {config.dry_run}")
print()


def print_step(record: StepRecord) -> None:
    print(f"=== Step {record.step + 1} ===")
    print(f"Command:   {record.request.command}")
    print(f"Exit code: {record.result.exit_code}")
    if record.result.truncated:
        print("[output truncated]")
    print(record.result.output or "(no output)")
    print()


start = time.time()
episode = run_episode(config, on_step=print_step)
elapsed = time.time() - start

print(f"Episode complete — {len(episode.steps)} steps in {elapsed:.1f}s")
