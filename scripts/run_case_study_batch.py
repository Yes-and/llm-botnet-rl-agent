"""
Run repeated case-study episodes across one or more model configs and
summarize outcomes in one CSV — for cross-model thesis benchmarking.

Reuses agent.loop.run_episode() and the success-detection logic in
scripts/case_study_common.py (same mechanism as scripts/run_case_study.py),
just looped N times per config with results collected instead of eyeballed.
No seeding: each repeat is expected to vary with the model's own sampling —
that variance across runs is exactly what's being measured.

Usage:
    python scripts/run_case_study_batch.py experiments/configs/s004-case-telnet-*.yml --repeats 10
"""

import argparse
import csv
from pathlib import Path

from dotenv import load_dotenv

from scripts.case_study_common import load_config, run_case_study

load_dotenv()

_FIELDNAMES = [
    "config", "model", "run", "success", "first_success_step", "total_steps",
    "stop_reason", "elapsed_s", "prompt_tokens", "completion_tokens", "malformed_calls",
]

parser = argparse.ArgumentParser(description="Run repeated case-study episodes and summarize results.")
parser.add_argument("configs", type=Path, nargs="+", help="One or more YAML task configs")
parser.add_argument("--repeats", type=int, default=10, help="Runs per config (default: 10)")
parser.add_argument("--out-dir", type=Path, default=Path("experiments/results/batch"),
                     help="Where to write per-run logs and summary.csv (default: experiments/results/batch)")
args = parser.parse_args()

args.out_dir.mkdir(parents=True, exist_ok=True)
summary_path = args.out_dir / "summary.csv"
write_header = not summary_path.exists()

with open(summary_path, "a", newline="") as summary_f:
    writer = csv.DictWriter(summary_f, fieldnames=_FIELDNAMES)
    if write_header:
        writer.writeheader()

    for config_path in args.configs:
        episode_config, exploit_type = load_config(config_path)
        run_dir = args.out_dir / config_path.stem
        run_dir.mkdir(exist_ok=True)
        for i in range(1, args.repeats + 1):
            print(f"=== {config_path.stem} ({episode_config.model}) run {i}/{args.repeats} ===")
            # run_case_study() never raises — a mid-episode crash (rate limit, overload,
            # connection drop) is caught internally and reported with accurate partial
            # progress, so one bad run can't take down the rest of the batch.
            result = run_case_study(episode_config, exploit_type, run_dir / f"run{i}.log")
            row = {"config": config_path.stem, "model": episode_config.model, "run": i, **result}
            # fails fast on key drift between run_case_study()'s return dict and _FIELDNAMES
            writer.writerow({k: row[k] for k in _FIELDNAMES})
            summary_f.flush()
            outcome = f"SUCCESS step {result['first_success_step']}" if result["success"] else "no success"
            print(f"-> {outcome} ({result['total_steps']} steps, {result['elapsed_s']}s, "
                  f"{result['prompt_tokens']}+{result['completion_tokens']} tok)\n")

print(f"\nSummary written to {summary_path}")
