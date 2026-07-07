# ADR 011: Action-Duration Head — Multi-Try Blocks for Chained Exploits

**Status:** Accepted

**Supersedes:** part of the REINFORCE step semantics in ADR 007 (Decision 1); part of the joint distribution in ADR 010.

## Context

Training on scenario-003 plateaus at 0-1 exploits per episode, and it is always Redis. Redis (and MongoDB) succeed with a single correct command. SSH/FTP/Telnet require a *chain* of correct commands within one attack attempt — e.g. discover the wordlist path, run `hydra` with it, then connect and confirm shell access with `id`. The policy picks one `(host, action)` pair per environment step with no notion of "keep going" on the same target: a multi-step chain only completes if the host/action heads happen to re-select the same pair several times in a row by chance. Early in training that coincidence is rare, so the chain rarely finishes, so there is rarely a reward to reinforce it, so the policy never learns it is worthwhile.

Credit assignment itself is not the problem: `_compute_returns` in `scripts/train.py` already computes proper discounted reward-to-go, so a later reward already backpropagates to earlier steps within the same episode. The gap is exploration — the chain needs to actually *happen* before that machinery has anything to reinforce.

## Decision

Add a third factored head, `duration`, sampled after `action`:

```
π(host, action, duration | state) = π_host(host|state) · π_action(action|host,state) · π_duration(duration|host,action,state)
```

Concretely:

```
h = f_trunk(state)
host_slot ~ Categorical(softmax(W_h · h))

action_input = [h ∥ x_host]  (x_host = selected host features, or zero vector for broadcast slots — see ADR 010)
action ~ Categorical(softmax(W_a · action_input))

duration_input = [action_input ∥ one_hot(action)]
duration_idx ~ Categorical(softmax(W_d · duration_input))
duration = DURATION_OPTIONS[duration_idx]        # (1, 2, 3, 5)
```

The environment then executes up to `duration` consecutive primitive commands against the same `(host, action)`, stopping early once that action's goal is met:

- Every action except the brute-force ones: its `ExploitEvent` fires (existing dedup logic, unchanged).
- `BRUTE_FORCE_SSH`/`FTP`/`TELNET`: these never emit an `ExploitEvent` themselves (see `docs/features/rl-parser.md` — `_parse_hydra` only sets `creds_found`; the actual exploitation event happens later via `CONNECT_*`). Their goal is `creds_found` becoming true.

A block also ends early if the episode's step budget (`max_steps`) is exhausted mid-block, or if a try comes back as a skip (LLM error, invalid host) — a skip on the very first try ends the block exactly like today's single-try skip; a skip after real tries keeps whatever reward was already earned.

## Why REINFORCE remains valid

`log π(host, action, duration | state) = log π_host + log π_action + log π_duration` — a chain-rule decomposition of the joint, no approximation, extending the same reasoning ADR 010 used to add the action-on-host conditioning. Gradients flow through all three terms normally; no gradient needs to pass through the discrete samples themselves.

What *does* change is what "one step" means for the REINFORCE update in ADR 007's `∇J = Σ_t G_t · ∇ log π(a_t, h_t | s_t)`: `t` now indexes an action-duration **block** — one policy decision, spanning anywhere from 1 to `max(duration_options)` primitive commands — rather than a single primitive environment command. `r_t` (and hence `G_t`) is the *sum* of that block's primitive rewards. This is standard semi-MDP/options-style treatment: a block that ran 3 primitive commands still contributes exactly one `(log_prob, reward)` term to the trajectory. `_compute_returns` needs no code changes at all — it discounts across blocks exactly as it previously discounted across primitive steps.

## Implementation

- `duration_options: tuple[int, ...] = (1, 2, 3, 5)` — config-driven constructor param on `Policy` (`rl/policy.py`), mirroring how `conditioned_action_head` is already config-driven. Kept small and discrete rather than continuous — easier to train reliably.
- `Policy.duration_head` is fed `action_input` (the same input the action head received — so it inherits host-conditioning when `conditioned_action_head=True`) concatenated with a one-hot of the sampled action. This conditioning is deliberate: an *unconditioned* duration head would reproduce the exact failure mode ADR 010 fixed for the action head — learning "duration=5 is globally good" and misapplying it to trivial one-shot actions like `PROBE_REDIS`.
- `Environment.step_block(action, host_idx, max_tries)` (`rl/environment.py`) loops the existing single-command logic — extracted into `Environment._try_once` — up to `max_tries` times, breaking early per the per-action goal check above, on episode-budget exhaustion, or on a skip. `Environment.step()` is now simply `step_block(..., max_tries=1)`, a fully backward-compatible special case (existing tests pass unmodified).
- Each primitive try still increments the episode's step budget (`max_steps`). Asking for more tries than needed has a real cost — this is what makes the duration choice learnable rather than degenerate to always-max.
- `scripts/train.py` appends exactly **one** `(log_prob, block_reward)` pair per block to the REINFORCE trajectory lists, not one per primitive try.
- Startup validation in `scripts/train.py`: raises if `context_window < max(duration_options)`. `Environment._prune_messages` trims the conversation to the last `context_window` exchanges *episode-wide* — it has no concept of a block boundary. A block longer than the window would lose visibility into its own earlier tries partway through, silently defeating the mechanism it exists to enable. `duration_options` is capped at `5` specifically so a `context_window: 5` config satisfies this with no slack.
- New CSV columns for observability: `rewards.csv` gains `tries_<action>` (total primitive tries spent on that action per episode, alongside the existing `act_<action>` counts — `tries_X / act_X` gives average tries per block); `steps.csv` gains a `tries_used` column per block row.

## Alternatives considered

**Per-primitive-step resampling of the same block-level log-prob.** Instead of one aggregated `(log_prob, reward)` entry per block, record the same log-prob once per primitive try, each paired with that try's own reward. Rejected: this double-counts one decision's gradient contribution proportional to how many tries the block happened to run, biasing the policy toward or against long durations for reasons unrelated to actual reward quality.

**Continuous duration output** (e.g. a scalar from a learned Gaussian). Rejected as harder to train reliably than a small discrete menu, and unnecessary — a handful of options (1, 2, 3, 5) covers the observed range from single-shot exploits (Redis, Mongo) to short chains (SSH/FTP/Telnet).

**Unconditioned (parallel) duration head.** Rejected for the same reason ADR 010 rejected a parallel action head: it can only learn a single global try-budget, not "this specific action needs more room."

## Expected outcome

The policy should learn per-action try-budgets — `duration≈1` for actions that already succeed in one command (`PROBE_REDIS`, `PROBE_MONGO`), and a larger value for actions that need a chain of correct commands within one attempt (`BRUTE_FORCE_SSH`/`FTP`/`TELNET`) — rather than relying on the host/action heads to coincidentally re-select the same target several steps in a row. `tries_<action>` in `rewards.csv` should show this divergence emerging over training.

## Superseded sections in ADR 007

| Section | What changed |
|---|---|
| Decision 1 (Algorithm), update rule `∇J = Σ_t G_t · ∇ log π(a_t, h_t \| s_t)` | `t` now indexes an action-duration block (one policy decision spanning 1..`max(duration_options)` primitive commands), not a single primitive environment step. `r_t` is the sum of that block's primitive rewards. |

## Superseded sections in ADR 010

| Section | What changed |
|---|---|
| Joint distribution `π(host, action \| state) = π_host(host\|state) · π_action(action\|host,state)` | Gains a third factor: `π(host, action, duration \| state) = π_host · π_action(·\|host,state) · π_duration(·\|host,action,state)` |
