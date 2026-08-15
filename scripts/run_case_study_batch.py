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
import subprocess
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from scripts.case_study_common import _next_free, load_config, run_case_study, split_config_stem

load_dotenv()

_FIELDNAMES = [
    "config", "model", "run", "success", "first_success_step", "total_steps",
    "stop_reason", "elapsed_s", "prompt_tokens", "completion_tokens", "malformed_calls",
]

parser = argparse.ArgumentParser(description="Run repeated case-study episodes and summarize results.")
parser.add_argument("configs", type=Path, nargs="+", help="One or more YAML task configs")
parser.add_argument("--repeats", type=int, default=10, help="Runs per config (default: 10)")
parser.add_argument("--out-dir", type=Path, default=None,
                     help="Where to write per-run logs and summary.csv "
                          "(default: experiments/results/<scenario-slug>/<date>-batch, "
                          "auto-numbered on collision — see CLAUDE.md's Experiment Conventions; "
                          "pass this explicitly to label the batch's purpose, e.g. ...-confirm-glm52)")
args = parser.parse_args()

if args.out_dir is not None:
    out_dir = args.out_dir
else:
    scenario_slug, _ = split_config_stem(args.configs[0].stem)
    out_dir = _next_free(Path("experiments/results") / scenario_slug / f"{date.today().isoformat()}-batch")
args.out_dir = out_dir

args.out_dir.mkdir(parents=True, exist_ok=True)
summary_path = args.out_dir / "summary.csv"
write_header = not summary_path.exists()

with open(summary_path, "a", newline="") as summary_f:
    writer = csv.DictWriter(summary_f, fieldnames=_FIELDNAMES)
    if write_header:
        writer.writeheader()

    for config_path in args.configs:
        episode_config, exploit_type, target_container = load_config(config_path)
        run_dir = args.out_dir / config_path.stem
        run_dir.mkdir(exist_ok=True)
        for i in range(1, args.repeats + 1):
            if target_container:
                # Repeated brute-force load against the same long-lived target
                # container degrades it over a batch (confirmed 2026-07-28: telnetd
                # stops accepting new connections after enough cumulative load,
                # without the container process ever exiting — restart: policies
                # never catch this). Recreate it fresh before every single repeat
                # rather than relying on the container to survive N runs unattended.
                print(f"Restarting {target_container}...")
                subprocess.run(["docker", "restart", target_container], check=True)
                time.sleep(3)  # let the target's services finish coming back up
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
