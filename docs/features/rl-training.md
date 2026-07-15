# RL Training Loop

**Status:** Implemented (ADR 014 Phase 1 — worker only; host selection is scripted-random, not learned)

## Overview

`scripts/train.py` runs the REINFORCE training loop. Each episode works through the discovered host pool one engagement at a time; each engagement collects a sub-trajectory using the policy network. Returns are computed per-engagement (reset at each engagement boundary) rather than once per episode, and a single gradient update is applied at episode end over the full concatenated trajectory.

See `docs/adr/007-rl-algorithm-and-policy-design.md` for the original algorithm rationale and `docs/adr/014-hierarchical-single-host-engagement.md` for the engagement-scoped return design this loop now implements.

## Algorithm

**Update rule** (unchanged in form; scope of `t` changed — see below):

```
loss = -Σ_t  G_t · log π(a_t | s_t, host_t)
```

**Engagement-scoped returns (ADR 014):** `G_t = Σ_{k=t}^{T_engagement} γ^{k-t} · r_k` is the discounted return from step `t` to the *end of that engagement*, not the end of the episode. `_compute_returns` takes a list of per-engagement reward lists and resets the running discounted sum to zero at each engagement boundary before concatenating. This is the fix for cross-host reward bleed (ADR 014 Context #2): an exploit on host B no longer backpropagates into a previous engagement on host A.

**Baseline:** if `use_baseline: true`, the mean return across the *whole episode's* concatenated returns is subtracted before the update — unchanged from before ADR 014.

## Episode Loop

```
while not episode done:
    if pool is empty: break                       # nothing left to engage

    host_ip = random.choice(known_hosts)           # Phase 1: selector not learned
    env.start_engagement(host_ip)
    host_idx = known_hosts.index(host_ip)

    while not engagement done and not episode done:
        action, log_prob, entropy = policy.sample(state, host_idx)
        state, reward, done, info = env.interact(action)
        # non-skip steps: append to this engagement's log_probs/rewards
        engagement_done = info["engagement_done"]

    # this engagement's non-skip rewards become one segment for _compute_returns
```

At episode end: `_compute_returns(engagement_rewards, gamma)` → one gradient update over the concatenated trajectory.

A skipped interaction step (LLM error, timeout) is excluded from the trajectory (`if not skip:`) exactly as before ADR 014 — it contributes no `log_prob`/reward and doesn't end the engagement, though it still counts against the per-engagement safety cap (see `docs/features/rl-environment.md`).

## Config

One YAML file per run under `experiments/configs/`. Training-specific fields:

| Field | Default | Description |
|---|---|---|
| `num_episodes` | — | Required. Number of episodes to train. |
| `gamma` | 0.99 | Discount factor, applied within each engagement segment. |
| `learning_rate` | 0.001 | Adam learning rate. |
| `use_baseline` | true | Subtract mean episode return to reduce variance. |
| `hidden_dim` | 128 | Policy MLP hidden layer width. |
| `num_layers` | 2 | Policy MLP depth. |
| `save_every` | 10 | Checkpoint interval (episodes). |
| `results_dir` | `experiments/results` | Root dir for checkpoints and logs. |
| `seed_python` | 42 | Python random seed — also seeds the Phase 1 random host selector. |
| `seed_torch` | 42 | PyTorch seed. |
| `grad_clip` | 1.0 | Max gradient norm (`clip_grad_norm_`). |
| `entropy_coeff` | 0.0 | Entropy bonus weight; `loss -= entropy_coeff * entropy`. |
| `max_engagement_steps` | 10 | `EnvironmentConfig` field — safety cap per engagement (ADR 014). |

`conditioned_action_head` and `duration_options` (pre-ADR-014 fields) are no longer read — the policy's action head is unconditionally host-conditioned, and the duration head is retired (single-host persistence subsumes multi-try budgets). Old configs that still set these keys are unaffected; the keys are just ignored.

All other environment fields from `EnvironmentConfig` are also required (`container_name`, `max_steps`, `model`, etc.) — see `docs/features/rl-environment.md`.

## Checkpoints

Saved to `experiments/results/<config-stem>/checkpoint_ep<N>.pt`. Each checkpoint contains:
- `policy_state_dict` — network weights
- `optimizer_state_dict` — Adam state
- `episode` — episode number at save time
- `config` — full raw config dict for reproducibility

`--resume checkpoint.pt` restores `policy_state_dict` and `optimizer_state_dict` verbatim. Known gap: `optimizer_state_dict` includes the checkpoint's `learning_rate` — a changed `learning_rate` in the resume config is silently ignored (`grad_clip`/`entropy_coeff`/`gamma`/`num_episodes` are not affected, they're not part of optimizer state). Not fixed as of 2026-07-09; see project memory. A checkpoint saved before ADR 014 (old policy architecture — host/duration heads) cannot be resumed with the current `Policy` — `policy_state_dict` shapes won't match.

## Structured Output

- `rewards.csv` — one row per episode: `total_reward`, `loss`, `exploit_count`, `engagements` (count of engagements this episode), `entropy`, plus `act_<action>` counts. No more `tries_<action>` columns — every interaction step is exactly one primitive command now, so there's nothing to aggregate.
- `steps.csv` — one row per interaction step: `episode`, `step`, `host`, `action`, `reward`, `engagement_done`. One row = one primitive command, always (no more multi-try blocks to backfill across).
- `train.transcript.log` — one human-readable block per interaction step (thinking, command, output) — see `docs/features/logging.md`.

## Analysis Tooling

`scripts/plot_heatmap.py <results_dir> --mode {step,episode,returns}`:
- `step` — action category per `(episode, step)` from `steps.csv`.
- `episode` — action mix fraction per episode from `rewards.csv`.
- `returns` — discounted `G_t` per `(episode, step)` from `steps.csv`.

Pre-ADR-014 runs had one `steps.csv` row per multi-try *block*, logged at the block's final step — `step`/`returns` modes backfilled each row across the tries it actually consumed (`tries_used` column). Post-ADR-014 runs have exactly one row per primitive step, so `tries_used` is absent and the backfill degenerates to its already-existing fallback (`tries = 1` when the column is missing) — no plotting-script change was needed for this, it was already defensive. The `ABANDON` action has its own heatmap category (`abandon`, distinct from the `nothing`/skip category) so a policy learning to walk away from bad hosts is visually distinguishable from one that's just failing API calls.

## Usage

```bash
python scripts/train.py experiments/configs/s002-train-001.yml
# Log defaults to experiments/results/<run_id>/train.log

python scripts/train.py experiments/configs/s002-train-001.yml --log-file custom.log
```

## Files

- `scripts/train.py` — training loop
- `scripts/plot_heatmap.py` — analysis/visualization
- `experiments/configs/s002-train-001.yml` — first training config (scenario-002, 100 episodes)
- `rl/policy.py` — policy network
- `docs/adr/007-rl-algorithm-and-policy-design.md` — original algorithm design decisions
- `docs/adr/014-hierarchical-single-host-engagement.md` — engagement-scoped returns, current episode structure
