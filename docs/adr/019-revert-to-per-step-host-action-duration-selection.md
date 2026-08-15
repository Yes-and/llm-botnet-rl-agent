# ADR 019: Revert to Per-Step (Host, Action, Duration) Selection

**Status:** Accepted

## Context

The thesis paper cites s001–s003 as the "full RL training" experiments. Those numbers were produced under the pre-ADR-014 design — the policy freely picks `(host, action, duration)` every environment step (ADR 007/010/011). ADR-014's hierarchical single-host engagement was implemented and evaluated afterward, but excluded from the submitted paper for space. Left as the live code, the repository diverges from what the paper describes: cloning and running it today produces a different system than the one the thesis reports on.

## Decision

Restore the pre-ADR-014 `(host, action, duration)`-per-step architecture as the live/default design across `rl/actions.py`, `rl/policy.py`, `rl/environment.py`, `rl/state.py`, `scripts/train.py`, `scripts/run_rl_episode.py`, `scripts/plot_heatmap.py`, and their tests — built forward from the pre-ADR-014 code (`d89c363^`), not a blind `git revert`, so that bug fixes and improvements which landed on top of ADR-014 are kept where they're actually independent of it:

- The action/vulnerability credit-assignment fix (originally `c6e7760`): reward is withheld when the exploit that fires doesn't match the sampled action's intended vulnerability (e.g. `BRUTE_FORCE_SSH` sampled, but a `CONNECT`-style exploit actually landed).
- The `CONNECT_FTP` mask fix (`658a2fd`): FTP's exploitable path here is anonymous login, so gating `CONNECT_FTP` on `creds_found` made it structurally unreachable.
- The SSH instruction wording fix (`87cd064`): dropped a misleading "using any credentials you have discovered" phrase from the `CONNECT_SSH` instruction.
- ADR 015's visitation-count exploration bonus, in full — reshaped from its engagement-scoped nested-list form back to the flat per-episode list `_compute_returns` already used pre-ADR-014.

`rl/parser.py` needed no changes — it was never touched by ADR-014, so its reward-parsing fixes (Mongo/FTP regexes, gateway-hostname filtering) already apply to the restored design as-is.

## Explicitly not restored

- **`ABANDON` action / `MIN_STEPS_BEFORE_ABANDON`** — only meaningful because ADR-014 removed the ability to pick a different host next step. Under free per-step selection, choosing a different `(host, action)` pair already covers "give up on this host"; a dedicated abandon action would be redundant.
- **The `full_action_space` experimental toggle** — postdates ADR-014 by a day, was an open experiment (not a settled fix), and isn't part of the design that produced the paper's numbers.
- **The scripted single initial-scan (`_scripted_initial_scan`) and its retry/exception-widening logic** — doesn't exist pre-ADR-014, where `SCAN_NETWORK` is a normal policy-chosen action. Verified the pre-ADR-014 `_try_once()` already has a broad `except Exception` catch-all for LLM-call failures, so there was nothing to backport from the follow-up commit that widened exception handling specifically inside the scripted-scan function.
- **The single-active-host engagement machinery in general** — `start_engagement()`/`interact()`/`engagement_progress`/engagement-scoped return discounting. Compromised hosts are handled the way they were pre-ADR-014: they stay in state (visible) but get masked out of the policy's `host_head` via the existing `shell_access` feature (ADR 012) — no pool removal needed.

## Alternatives considered

**Deleting ADR-014's doc entirely.** Rejected — `docs/adr/` is append-only by house convention. ADR-014 stays as an honest record of a decision that was built, evaluated, and not adopted for the thesis; its status line now points here.

**`git revert d89c363` plus replaying the 11 follow-up commits.** Rejected — most of the follow-ups (`f9aacc7`, `5535ecc`, most of `49f571d`) are architecturally coupled to ADR-014's engagement concept and wouldn't apply cleanly (or would apply but be semantically meaningless) against the older code. Building forward from the pre-ADR-014 snapshot with targeted, individually-justified patches is auditable file-by-file and doesn't risk silently reintroducing an already-fixed bug or silently dropping one.

## Consequences

- Historical run data under `experiments/results/` produced during the ADR-014 window used a different CSV schema (`host`, `engagement_done` columns; no `tries_used`) than what the restored `scripts/train.py`/`scripts/plot_heatmap.py` write. That's expected — those are historical artifacts from an evaluated-and-set-aside design, not live data the reverted code needs to consume. No compatibility shim was added (YAGNI).
- `docs/features/rl-environment.md`, `rl-policy.md`, `rl-training.md`, `rl-parser.md` were reverted to describe the restored design (see those docs' own history for the ADR-014-era phrasing they replace).
