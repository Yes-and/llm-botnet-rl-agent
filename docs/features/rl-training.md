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

**Visitation-count exploration bonus (optional, `visitation_bonus_coeff`, default `0.0` — off):** an intrinsic per-step reward `1/sqrt(count)`, where `count` is a persistent, whole-run (never per-episode) count of how many times the policy has selected that action, including the current occurrence. Motivated by a `full_action_space` run's entropy collapse onto 3 of 12 actions — vanilla REINFORCE with no baseline reinforces whatever earns positive return early, and the flat `entropy_coeff` bonus (uniform pressure on the whole distribution) didn't stop it; a count-based bonus targets specifically under-tried actions instead. Computed as a second, parallel per-episode reward stream (`intrinsic_rewards`, same shape as the real `rewards` list) run through the same `_compute_returns(gamma)`, then folded into the loss with its own coefficient — deliberately kept separate from the real `reward` fed into `rewards.csv`'s `total_reward`, so that column keeps meaning "real reward only":

```
loss = -Σ_t (G_t + visitation_bonus_coeff · G_t^intrinsic) · log π(a_t, h_t | s_t)  −  entropy_coeff · entropy_bonus
```

`use_baseline` mean-centering is applied only to the real returns, not the intrinsic stream. The visitation counter is process-lifetime only — not saved into or restored from checkpoints, so a `--resume`'d run restarts it from zero (same category of known gap as the `learning_rate` resume issue below).

## Episode Loop

At each step:
1. `policy.sample(state, known_host_count)` → `(action, host_slot, duration, log_prob, entropy)` (ADR 011 — `duration` is a discrete try-budget, `entropy` sums all three heads' entropies)
2. Translate `host_slot` to `host_idx`: slots 0 and 1 (no_host, all_hosts) are broadcast — `host_idx` is ignored. Slots 2+ map to `host_idx = host_slot - 2`.
3. `env.step_block(action, host_idx, duration)` → `(state, reward, done, info)` — see `docs/features/rl-environment.md`'s "Duration (Multi-Try Blocks)" section. One block is one training step regardless of how many primitive tries it took.

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
| `grad_clip` | 1.0 | Max gradient norm (`clip_grad_norm_`). |
| `entropy_coeff` | 0.0 | Entropy bonus weight; `loss -= entropy_coeff * entropy`. |
| `conditioned_action_head` | false | ADR 010 — condition the action head on the selected host's features. |
| `duration_options` | `(1, 2, 3, 5)` | ADR 011 — discrete try-budgets the duration head can choose from. Must satisfy `context_window >= max(duration_options)` (checked at startup). |
| `visitation_bonus_coeff` | 0.0 | Weight on the count-based exploration bonus described above. `0.0` (default) is a full no-op — no behavior change, no extra computation cost beyond bookkeeping. |

All environment fields from `EnvironmentConfig` are also required (`container_name`, `max_steps`, `model`, etc.) — see `docs/features/rl-environment.md`.

## Checkpoints

Saved to `experiments/results/<scenario>/<config-stem>/checkpoint_ep<N>.pt` — nested under the scenario number (e.g. `s003`) parsed from the config filename, same spirit as the case-study track's `<scenario>-<exploit>/` folders (`docs/features/scenario-*.md`), just scenario-only since an RL config targets a whole multi-target scenario, not one exploit type. A config that doesn't start with `s<digits>-` falls back to using its own stem as the scenario folder too (a harmless no-op nesting, not an error). Each checkpoint contains:
- `policy_state_dict` — network weights
- `optimizer_state_dict` — Adam state
- `episode` — episode number at save time
- `config` — full raw config dict for reproducibility

`--resume checkpoint.pt` restores `policy_state_dict` and `optimizer_state_dict` verbatim. Known gap: `optimizer_state_dict` includes the checkpoint's `learning_rate` — a changed `learning_rate` in the resume config is silently ignored (`grad_clip`/`entropy_coeff`/`gamma`/`num_episodes` are not affected, they're not part of optimizer state). Not fixed as of 2026-07-09; see project memory.

## Structured Output

- `rewards.csv` — one row per episode: `total_reward`, `loss`, `exploit_count`, `entropy`, `visitation_bonus_sum` (raw, undiscounted per-episode sum of the exploration bonus — populated regardless of whether `visitation_bonus_coeff` is on, so it can be inspected without having enabled it), plus `act_<action>` and `tries_<action>` counts.
- `steps.csv` — one row per RL decision (block, not primitive command): `episode`, `step`, `action`, `reward`, `tries_used`, `visitation_count` (running whole-run count for that action, post-increment), `visitation_bonus` (`1/sqrt(visitation_count)` for that step). `step` is the block's *final* primitive step count, not its first.

## Analysis Tooling

`scripts/plot_heatmap.py <results_dir> --mode {step,episode,returns}`:
- `step` — action category per `(episode, step)` from `steps.csv`.
- `episode` — action mix fraction per episode from `rewards.csv`.
- `returns` — discounted `G_t` per `(episode, step)` from `steps.csv`.

Since one `steps.csv` row covers a whole multi-try block (ADR 011) but is logged at a single `step`, `step` and `returns` modes backfill each row across `step - tries_used .. step` so the plotted block spans the primitive steps it actually consumed. `returns` mode additionally excludes first-try-skip rows (`API_TIMEOUT`/`NO_TOOL_CALL`/`INVALID_HOST_IDX`/`UNEXPECTED_ERROR`) before computing `G_t`, since those never get a `log_prob`/reward pair in the real training trajectory (`if not skip: rewards.append(reward)` in the Episode Loop above) — including them would discount over a different sequence than the one the policy gradient actually used. Both fixes landed 2026-07-09; heatmaps generated from runs before that date on the old script version should be regenerated.

## Usage

```bash
python scripts/train.py experiments/configs/s002-train-001.yml
# Log defaults to experiments/results/<scenario>/<run_id>/train.log

python scripts/train.py experiments/configs/s002-train-001.yml --log-file custom.log
```

## Files

- `scripts/train.py` — training loop
- `scripts/plot_heatmap.py` — analysis/visualization
- `experiments/configs/s002-train-001.yml` — first training config (scenario-002, 100 episodes)
- `rl/policy.py` — policy network
- `docs/adr/007-rl-algorithm-and-policy-design.md` — design decisions
- `docs/adr/010-conditioned-action-head.md`, `docs/adr/011-action-duration-head.md` — later policy changes
