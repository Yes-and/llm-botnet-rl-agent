# ADR 015: Visitation-Count Exploration Bonus

**Status:** Accepted

## Context

The `full_action_space` (fully unmasked) run (`s003-train-minimax-m27-full-action-space-001`) collapsed entropy from 2.4572 to 0.2143 over 50 episodes, with the sampled action distribution dying down to only 3 of 12 actions (`PROBE_REDIS`/`PROBE_MONGO`/`ABANDON`) by ~episode 38. Vanilla REINFORCE with `use_baseline: false` and no other variance reduction reinforces whatever earns positive return early; every episode an action isn't sampled is one less chance to ever learn its value. The existing `entropy_coeff` bonus applies uniform pressure across the whole action distribution and wasn't enough to prevent the collapse.

A masked-baseline comparison run (`masked-baseline-002`) subsequently showed that structural `is_valid()` masking alone prevents *total* starvation — brute-force actions stayed nonzero in every episode, unlike the unmasked run's complete die-off. That result was the condition this ADR's proposal was deliberately waiting on before implementation (see `docs/features/rl-training.md`'s prior discussion, and project memory). With that comparison in hand, the bonus is being built anyway, as a toggleable experiment to pair with a future unmasked re-run.

## Decision

Add an optional, off-by-default count-based exploration bonus to `scripts/train.py`'s policy-gradient loss:

```
bonus(count) = 1 / sqrt(count)
```

where `count` is a persistent, **whole-run** count of how many times the policy has selected that action (a plain `Counter()` declared once before the episode loop, never reset per-episode — deliberately not the existing per-episode `tried_<action>` state in `EpisodeState`, which resets every episode and would give a fresh full-strength push every time rather than converging as real training experience accumulates).

Implementation shape:
- Counted **regardless of `skip`** — the policy still chose the action even if the LLM call downstream failed to produce a usable command, and that choice is what should influence future exploration pressure.
- The bonus value feeding the loss is only appended on non-skip steps, to stay positionally aligned with `log_probs` (which is also skip-gated) — the counter increments unconditionally, but the loss-relevant intrinsic-reward stream mirrors exactly which steps contribute a gradient term.
- Built as a second, parallel per-engagement reward stream (`intrinsic_rewards`, same shape as `engagement_rewards`) and run through the *same* `_compute_returns(gamma)` helper, so engagement-boundary discounting is identical between the real and intrinsic streams.
- Folded into the loss as its own coefficient, additive with the real return before the log-prob weighting:
  ```
  loss = -(log_probs · (returns + visitation_bonus_coeff · intrinsic_returns)).sum() - entropy_coeff · entropy_bonus
  ```
- **Not** mixed into the raw per-step `reward` written to `rewards.csv`/used for `total_reward` — that column must keep meaning "real reward only," separable from exploration shaping (matters for reporting genuine exploit reward vs. shaping in thesis analysis).
- `use_baseline` mean-centering applies only to the real returns, not the intrinsic stream (baseline is for variance-reducing the real return signal; the bonus is already small and self-limiting via `1/sqrt`).
- New config field `visitation_bonus_coeff` (default `0.0`) — zero is a complete no-op, no existing config needs to change.
- Granularity is a **global per-action** count, not per-`(host, action)` — the motivating failure mode was a global, cross-host action-type collapse, and `MAX_HOSTS × 12 actions` would likely never converge within a 25-50 episode run.
- Logged at both granularities for later analysis: `steps.csv` gets `visitation_count`/`visitation_bonus` per step (populated regardless of whether the coefficient is on, so a run can be inspected retroactively for what the bonus *would* have been); `rewards.csv` gets `visitation_bonus_sum`, a raw per-episode total, next to `entropy`/`loss` for a quick trend glance.

## Alternatives considered

**Mixing the bonus directly into the real per-step reward.** Rejected in the original design discussion, before this ADR — would corrupt `rewards.csv`'s `total_reward`/`reward` columns as a measure of genuine exploit performance, conflating real signal with an exploration artifact specifically introduced to fight training dynamics, not the thing being measured for the thesis.

**Per-`(host, action)` visitation counts.** Rejected for now — more granular, but the state space (12 actions × up to `MAX_HOSTS` known hosts per episode) is large relative to a 25-50 episode run's total step budget; a global per-action count directly targets the failure mode actually observed (whole action categories dying out, not host-specific neglect). Worth revisiting if a future run shows host-specific starvation that a global count doesn't fix.

**Persisting the counter across `--resume`.** Rejected as out of scope for a first toggleable experiment — the counter is process-lifetime only and restarts from zero on a resumed run. Same category as the already-documented, already-deferred `learning_rate`-on-resume gap (see `docs/features/rl-training.md`'s Checkpoints section).

## Consequences

- `scripts/train.py`'s top-level side-effecting code (argparse, env checks, file I/O) is now wrapped in `if __name__ == "__main__":`, so the module can be imported without those side effects firing. Purely mechanical (module-level `if` blocks don't introduce a new scope in Python, so this doesn't change any variable's behavior) — done specifically to make `_compute_returns` and the new `_visitation_bonus` helper unit-testable, which they previously weren't (no test harness existed for `scripts/train.py` at all before this).
- `scripts` added to `[tool.setuptools.packages.find]` in `pyproject.toml` (alongside `agent`/`rl`) and given an `__init__.py`, so `from scripts.train import ...` resolves the same reliable way `rl`/`agent` imports already do, regardless of pytest invocation style or working directory. Requires re-running `pip install -e .` for the new package registration to take effect.
- New file `tests/test_train.py` — covers `_visitation_bonus` and, retroactively, `_compute_returns` (previously untested).
- `docs/features/rl-training.md` updated: Algorithm section (bonus description + updated loss formula), Config table (`visitation_bonus_coeff` row), Structured Output section (new `rewards.csv`/`steps.csv` columns).
- No run has yet been launched with `visitation_bonus_coeff > 0` — this ADR covers the mechanism, not a result. Intended pairing: a future unmasked (`full_action_space: true`) re-run, to test whether the bonus prevents the entropy collapse the original `full_action_space-001` run showed.
