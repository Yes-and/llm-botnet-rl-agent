"""
Run a fixed-task episode against a single target and report whether the
expected exploit succeeded, and after how many steps.

No RL/policy involved — reuses agent.loop.run_episode() directly, the same
mechanism as scripts/run_episode.py, plus a per-step success check. Used to
isolate raw LLM capability per exploit type from RL training dynamics.

For repeated runs across models (e.g. for aggregate thesis results), see
scripts/run_case_study_batch.py instead.

Usage:
    python scripts/run_case_study.py experiments/configs/s003-case-ssh.yml
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv

from scripts.case_study_common import load_config, run_case_study

load_dotenv()

parser = argparse.ArgumentParser(description="Run a single-target LLM capability case study.")
parser.add_argument("config", type=Path, help="Path to YAML task config")
parser.add_argument("--log-file", type=Path, default=None,
                     help="Full step output log (default: <config-name>.log)")
args = parser.parse_args()
log_path = args.log_file or Path(f"{args.config.stem}.log")

config, exploit_type = load_config(args.config)

print(f"Config:       {args.config}")
print(f"Task:         {config.task.strip()}")
print(f"Container:    {config.container_name}")
print(f"Exploit type: {exploit_type}")
print(f"Steps:        {config.max_steps}")
print(f"Model:        {config.model}")
print(f"Log file:     {log_path}")
print()

summary = run_case_study(config, exploit_type, log_path)

print()
print(f"Episode complete — {summary['total_steps']} steps in {summary['elapsed_s']}s")
print(f"Tokens: {summary['prompt_tokens']} prompt / {summary['completion_tokens']} completion"
      f" ({summary['malformed_calls']} malformed tool calls)")
if summary["stop_reason"] is not None:
    print(f"Ended early: {summary['stop_reason']}")
if summary["success"]:
    print(f"Result: SUCCESS on step {summary['first_success_step']}/{config.max_steps}")
else:
    print(f"Result: NO SUCCESS within {config.max_steps} steps")
print(f"Full step output saved to {log_path}")
