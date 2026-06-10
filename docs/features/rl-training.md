# RL Training Loop

**Status:** Implemented

## Overview

`scripts/train.py` runs the REINFORCE training loop. Each episode collects a full trajectory using the policy network, computes discounted returns, and updates the policy with a Monte Carlo policy gradient step.

See ADR 007 for the algorithm and policy design rationale.

## Algorithm

**Update rule:**

```
loss = -Σ_t  G_t · log π(a_t, h_t | s_t)
```

where `G_t = Σ_{k=t}^{T} γ^{k-t} · r_k` is the discounted return from step t.

**Baseline:** if `use_baseline: true`, the mean return across the episode is subtracted from each `G_t` before the update. This reduces variance without introducing a learned critic.

## Episode Loop

At each step:
1. `policy.sample(state, known_host_count)` → `(action, host_slot, log_prob)`
2. Translate `host_slot` to `host_idx`: slots 0 and 1 (no_host, all_hosts) are broadcast — `host_idx` is ignored. Slots 2+ map to `host_idx = host_slot - 2`.
3. `env.step(action, host_idx)` → `(state, reward, done, info)`

At episode end, returns are computed and a single gradient update is applied.

## Config

One YAML file per run under `experiments/configs/`. Training-specific fields:

| Field | Default | Description |
|---|---|---|
| `num_episodes` | — | Required. Number of episodes to train. |
| `gamma` | 0.99 | Discount factor. |
| `learning_rate` | 0.001 | Adam learning rate. |
| `use_baseline` | true | Subtract mean episode return to reduce variance. |
| `hidden_dim` | 128 | Policy MLP hidden layer width. |
| `num_layers` | 2 | Policy MLP depth. |
| `save_every` | 10 | Checkpoint interval (episodes). |
| `results_dir` | `experiments/results` | Root dir for checkpoints and logs. |
| `seed_python` | 42 | Python random seed. |
| `seed_torch` | 42 | PyTorch seed. |

All environment fields from `EnvironmentConfig` are also required (`container_name`, `max_steps`, `model`, etc.).

## Checkpoints

Saved to `experiments/results/<config-stem>/checkpoint_ep<N>.pt`. Each checkpoint contains:
- `policy_state_dict` — network weights
- `optimizer_state_dict` — Adam state
- `episode` — episode number at save time
- `config` — full raw config dict for reproducibility

## Known Limitations

- **No reward history file.** Per-episode rewards are logged to the log file and printed to stdout but not saved as a structured file (e.g. CSV). Plotting learning curves requires parsing the log. A `rewards.csv` or JSON file in the results dir should be added before doing multi-run analysis.

## Usage

```bash
python scripts/train.py experiments/configs/s002-train-001.yml
# Log defaults to experiments/results/<run_id>/train.log

python scripts/train.py experiments/configs/s002-train-001.yml --log-file custom.log
```

## Files

- `scripts/train.py` — training loop
- `experiments/configs/s002-train-001.yml` — first training config (scenario-002, 100 episodes)
- `rl/policy.py` — policy network
- `docs/adr/007-rl-algorithm-and-policy-design.md` — design decisions
