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

from scripts.case_study_common import _next_free, load_config, run_case_study, split_config_stem

load_dotenv()

parser = argparse.ArgumentParser(description="Run a single-target LLM capability case study.")
parser.add_argument("config", type=Path, help="Path to YAML task config")
parser.add_argument("--log-file", type=Path, default=None,
                     help="Full step output log (default: experiments/results/<scenario-slug>/<model>.log, "
                          "auto-numbered on collision — see CLAUDE.md's Experiment Conventions)")
args = parser.parse_args()
if args.log_file is not None:
    log_path = args.log_file
else:
    scenario_slug, model_slug = split_config_stem(args.config.stem)
    out_dir = Path("experiments/results") / scenario_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = _next_free(out_dir / f"{model_slug}.log")

config, exploit_type, _target_container = load_config(args.config)

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
