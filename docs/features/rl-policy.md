# RL Policy

**Status:** Implemented (ADR 014 Phase 1 — worker only; host selection is not learned)

## Overview

`rl/policy.py` implements the policy network used during REINFORCE training. It maps the current episode state tensor plus the currently active host to a distribution over the 12 interaction actions (see `rl/actions.py`), from which the training loop samples the instruction injected into the LLM prompt.

Host selection — which host to engage next — is **not** learned in Phase 1; it's a uniform random pick made by the training loop (`scripts/train.py`). The host head from the pre-ADR-014 design is retired for now; it returns in ADR 014 Phase 2 once a focused single-action worker is shown to complete exploit chains.

See `docs/adr/014-hierarchical-single-host-engagement.md` for the design rationale. ADR 007's original host-first factored-head design, ADR 010's conditioned action head, and ADR 011's duration head are all superseded by ADR 014 — see that ADR's "Superseded sections."

## Architecture

```
state [MAX_HOSTS, NUM_FEATURES]
    → flatten → [MAX_HOSTS * NUM_FEATURES]
    → shared MLP trunk → hidden [hidden_dim]
    → action head: softmax over [NUM_ACTIONS]
          input = concat(hidden, state[host_idx])   # active host's feature row
```

The action head is conditioned on the active host's feature vector unconditionally now — there is no more parallel/conditioned toggle (ADR 010's `conditioned_action_head` flag is gone). Every decision is host-scoped by construction in Phase 1, so conditioning is no longer optional.

## Action Masking

The mask is a direct reflection of `rl.actions.is_valid()` — no precondition logic is duplicated in `policy.py`. `_build_action_mask(host_row)` builds a `[NUM_ACTIONS]` boolean mask (`True` = invalid) by calling `is_valid()` for every action against the active host's current feature dict, and the resulting logits are soft-masked (pushed to a large negative value, not hard `-inf`) before softmax.

This mask is recomputed from the active host's live features on **every** call, which gives it the "dial-in" property central to Phase 1's design: right after the scripted discovery scan, only `is_alive` is known, so `is_valid()` only passes `SCAN_PORTS`, `PROBE_PORT`, and `ABANDON` — the policy is structurally forced into recon before anything else. As the engagement discovers open ports, services, and credentials, the corresponding brute-force/connect/probe actions unmask themselves; actions whose precondition is no longer relevant (e.g. `BRUTE_FORCE_SSH` once `creds_found` is `True`) mask back out. `ABANDON` is unconditionally valid (`is_valid(Action.ABANDON, ...)` always returns `True`), which also guarantees the mask never fully excludes every action — there's always a valid fallback even with nothing known about a host yet.

Host-level dedup (previously a `shell_access` mask on the host head, ADR 012) is gone from the policy entirely: a solved host is removed from the pool by the environment (`EpisodeState.remove`), so it's structurally impossible for the training loop to hand a compromised host's index to the policy again within the same episode.

## Sampling

```python
dist = Categorical(logits=self._action_logits(state, host_idx))
action_idx = dist.sample()
log_prob = dist.log_prob(action_idx)
entropy = dist.entropy()
```

At evaluation time, argmax is used via `policy.predict()`.

## Interface

```python
policy = Policy(hidden_dim=128, num_layers=2)

action, log_prob, entropy = policy.sample(state_tensor, host_idx)
action                    = policy.predict(state_tensor, host_idx)
```

`host_idx` is the row index into the `[MAX_HOSTS, NUM_FEATURES]` state tensor for the host the caller started an engagement on — obtained via `env._state.known_hosts().index(host_ip)` in `scripts/train.py`.

## Files

- `rl/policy.py` — implementation
- `tests/test_policy.py` — unit tests (shapes, is_valid()-based masking/dial-in behavior, host conditioning, log-prob, determinism)
- `docs/adr/014-hierarchical-single-host-engagement.md` — current design; retires the host and duration heads for Phase 1
- `docs/adr/007-rl-algorithm-and-policy-design.md`, `010-conditioned-action-head.md`, `011-action-duration-head.md`, `012-shell-access-mask-and-exploit-host-attribution.md` — superseded prior designs, kept for history (ADRs are append-only)
- `rl/actions.py` — action enum and `is_valid()`, the single source of truth for the mask
- `rl/state.py` — state tensor structure
- `rl/environment.py` — `interact()`/`start_engagement()`, which drive the sampled action
