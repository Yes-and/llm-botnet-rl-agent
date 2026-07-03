"""
Heatmap visualisation of policy action sequences across training.

Modes:
  step     (default)  Each cell = action category at (episode, step).
                      Reads steps.csv, or falls back to parsing train.log.
  episode             Each cell = fraction of steps in that action category per episode.
                      Reads rewards.csv.

Usage:
    python scripts/plot_heatmap.py experiments/results/s003-train-kimi-k25-001/
    python scripts/plot_heatmap.py experiments/results/s003-train-kimi-k25-001/ --mode episode
    python scripts/plot_heatmap.py experiments/results/s003-train-kimi-k25-001/ --out out.png
"""

import argparse
import re
import sys
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap

# ── Action → category ─────────────────────────────────────────────────────────

CATEGORIES = ["nothing", "scan", "probe", "brute", "exploit"]
COLORS     = ["#aaaaaa", "#4e9af1", "#f5a623", "#e74c3c", "#2ecc71"]

_ACTION_TO_CAT = {
    "DO_NOTHING":        "nothing",
    "SCAN_NETWORK":      "scan",   "SCAN_PORTS":        "scan",
    "PROBE_PORT":        "probe",  "PROBE_HTTP":        "probe",
    "PROBE_REDIS":       "probe",  "PROBE_MONGO":       "probe",
    "BRUTE_FORCE_SSH":   "brute",  "BRUTE_FORCE_FTP":   "brute",
    "BRUTE_FORCE_TELNET":"brute",
    "CONNECT_SSH":       "exploit","CONNECT_FTP":        "exploit",
    "CONNECT_TELNET":    "exploit",
}

# rewards.csv column → category
_COL_TO_CAT = {
    "act_do_nothing":        "nothing",
    "act_scan_network":      "scan",   "act_scan_ports":        "scan",
    "act_probe_port":        "probe",  "act_probe_http":        "probe",
    "act_probe_redis":       "probe",  "act_probe_mongo":       "probe",
    "act_brute_force_ssh":   "brute",  "act_brute_force_ftp":   "brute",
    "act_brute_force_telnet":"brute",
    "act_connect_ssh":       "exploit","act_connect_ftp":        "exploit",
    "act_connect_telnet":    "exploit",
}

def _cat_idx(cat: str) -> int:
    return CATEGORIES.index(cat)


# ── Log parser (fallback when steps.csv absent) ───────────────────────────────

_STEP_RE  = re.compile(r"\[Step\s+(\d+)/\d+\] (\w+)")
_RESET_RE = re.compile(r"=== Episode reset ===")


def _parse_log(log_path: Path) -> pd.DataFrame:
    rows: list[dict] = []
    episode = 0
    with open(log_path) as f:
        for line in f:
            if _RESET_RE.search(line):
                episode += 1
            else:
                m = _STEP_RE.search(line)
                if m and episode > 0:
                    rows.append({
                        "episode": episode,
                        "step":    int(m.group(1)),
                        "action":  m.group(2),
                    })
    if not rows:
        sys.exit(f"No step lines found in {log_path}. Check the log format.")
    return pd.DataFrame(rows)


# ── Episode mode ──────────────────────────────────────────────────────────────

def _plot_episode(results_dir: Path, out: Path) -> None:
    csv_path = results_dir / "rewards.csv"
    if not csv_path.exists():
        sys.exit(f"Not found: {csv_path}")

    df = pd.read_csv(csv_path)
    present = {col: cat for col, cat in _COL_TO_CAT.items() if col in df.columns}

    matrix = np.zeros((len(df), len(CATEGORIES)))
    for col, cat in present.items():
        matrix[:, _cat_idx(cat)] += df[col].values

    totals = matrix.sum(axis=1, keepdims=True)
    totals[totals == 0] = 1
    matrix /= totals

    fig, ax = plt.subplots(figsize=(5, max(4, len(df) * 0.18)))
    im = ax.imshow(matrix, aspect="auto", vmin=0, vmax=1,
                   cmap="Blues", interpolation="nearest")
    ax.set_xticks(range(len(CATEGORIES)))
    ax.set_xticklabels(CATEGORIES)
    ax.set_ylabel("Episode")
    ax.set_title("Action category mix per episode")
    plt.colorbar(im, ax=ax, label="Fraction of steps")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    print(f"Saved: {out}")


# ── Step mode ─────────────────────────────────────────────────────────────────

def _plot_step(results_dir: Path, out: Path) -> None:
    steps_csv = results_dir / "steps.csv"
    log_path  = results_dir / "train.log"

    if steps_csv.exists():
        df = pd.read_csv(steps_csv)
    elif log_path.exists():
        print(f"steps.csv not found — parsing {log_path}")
        df = _parse_log(log_path)
    else:
        sys.exit(f"Neither steps.csv nor train.log found in {results_dir}")

    df["cat_idx"] = df["action"].map(_ACTION_TO_CAT).map(_cat_idx)
    unknown = df["cat_idx"].isna()
    if unknown.any():
        print(f"Warning: {unknown.sum()} rows had unrecognised action names and will appear as missing.")

    num_episodes = int(df["episode"].max())
    num_steps    = int(df["step"].max())

    matrix = np.full((num_episodes, num_steps), -1, dtype=float)
    matrix[
        (df["episode"] - 1).values.astype(int),
        (df["step"]    - 1).values.astype(int),
    ] = df["cat_idx"].values

    masked = np.ma.masked_equal(matrix, -1)

    cmap = ListedColormap(COLORS)
    cmap.set_bad(color="white")
    norm = BoundaryNorm(range(len(CATEGORIES) + 1), len(CATEGORIES))

    fig_h = max(4, num_episodes * 0.15)
    fig_w = max(8, num_steps * 0.10)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.imshow(masked, aspect="auto", cmap=cmap, norm=norm, interpolation="nearest")
    ax.set_xlabel("Step")
    ax.set_ylabel("Episode")
    ax.set_title("Action category per step per episode")

    patches = [mpatches.Patch(color=COLORS[i], label=CATEGORIES[i]) for i in range(len(CATEGORIES))]
    ax.legend(handles=patches, bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0)

    fig.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Saved: {out}")


# ── CLI ───────────────────────────────────────────────────────────────────────

_parser = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
_parser.add_argument("results_dir", type=Path, help="Run results directory")
_parser.add_argument("--mode", choices=["step", "episode"], default="step")
_parser.add_argument("--out", type=Path, default=None,
                     help="Output PNG path (default: <results_dir>/heatmap_<mode>.png)")
_args = _parser.parse_args()

_out = _args.out or (_args.results_dir / f"heatmap_{_args.mode}.png")

if _args.mode == "episode":
    _plot_episode(_args.results_dir, _out)
else:
    _plot_step(_args.results_dir, _out)
