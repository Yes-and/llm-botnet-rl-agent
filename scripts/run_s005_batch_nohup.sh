#!/usr/bin/env bash
# Runs the 4-model scenario-005 (Mongo) batch in the background via nohup,
# so it survives an SSH disconnect. Swap the config list to reuse for scenario-006.
set -euo pipefail

OUT_DIR="experiments/results/s005-mongo/$(date +%F)-4model"
mkdir -p "$OUT_DIR"

nohup python scripts/run_case_study_batch.py \
  experiments/configs/s005-case-mongo-kimi-k3-openrouter.yml \
  experiments/configs/s005-case-mongo-qwen3-coder-480b.yml \
  experiments/configs/s005-case-mongo-qwen3-coder-30b.yml \
  experiments/configs/s005-case-mongo-minimax-m27.yml \
  --repeats 10 \
  --out-dir "$OUT_DIR" \
  > "$OUT_DIR/nohup.log" 2>&1 &

echo "Started, PID $!"
echo "Output dir: $OUT_DIR"
echo "Tail progress: tail -f $OUT_DIR/nohup.log"
echo "Check if still running: ps -p $!"
