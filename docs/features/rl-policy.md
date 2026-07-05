# RL Policy

**Status:** Implemented

## Overview

`rl/policy.py` implements the policy network used during REINFORCE training. It maps the current episode state tensor to a distribution over `(host, action)` pairs, from which the training loop samples to produce the instruction injected into the LLM prompt.

See ADR 007 for the original design rationale and ADR 010 for the conditioned action head extension.

## Architecture

```
state [MAX_HOSTS, NUM_FEATURES]
    → flatten → [MAX_HOSTS * NUM_FEATURES]
    → shared MLP trunk → hidden [hidden_dim]
    → host head: softmax over [MAX_HOSTS + 2] slots
    → action head: softmax over [NUM_ACTIONS]
          parallel mode:   input = hidden
          conditioned mode: input = concat(hidden, state[host_slot])
```

The `conditioned_action_head` config flag (default `false`) switches between the two modes.

## Host Head

Outputs a distribution over `MAX_HOSTS + 2` slots:

| Index | Meaning |
|---|---|
| 0 | `no_host` — for `DO_NOTHING` |
| 1 | `all_hosts` — for `SCAN_NETWORK` |
| 2 … MAX_HOSTS+1 | Discovered hosts, ordered by their randomly assigned tensor slot |

Host slots beyond `len(known_hosts())` are hard-masked to `-inf` before softmax. Slots are assigned randomly each episode reset so the policy must learn from feature content, not position.

## Action Head

Outputs a distribution over all 13 action types (see `rl/actions.py`). Structurally invalid `(host, action)` combinations (e.g. `SCAN_NETWORK` with a specific host slot) are soft-masked: logits are pushed to a large negative value, keeping near-zero probability without hard exclusion.

A single dynamic hard mask is applied at inference time: if a host's `shell_access` feature is `1`, all `CONNECT_*` actions (`CONNECT_SSH`, `CONNECT_FTP`, `CONNECT_TELNET`) are masked to `-inf` for that host slot. This prevents re-exploitation of already-compromised hosts — a case where the negative reward signal alone proved insufficient (s003 MiniMax M2.5 conditioned run, episode 49).

All other precondition checks (port open, creds found, etc.) are left to the learned reward signal rather than hard-masking, to avoid over-constraining the agent's exploration.

### Parallel mode (`conditioned_action_head: false`)

Action logits are computed solely from the trunk output. The action head cannot condition on the selected host's current features, limiting its ability to suppress actions on specific hosts that have already failed.

### Conditioned mode (`conditioned_action_head: true`)

After sampling the host slot, the selected host's feature vector is concatenated to the trunk output before computing action logits:

```
log π(host, action | state) = log π_host(host | state) + log π_action(action | host, state)
```

This enables per-host action preferences — e.g. suppressing `PROBE_REDIS` on a host where `tried_probe_redis=1` and `shell_access=0`. Broadcast slots (`no_host`, `all_hosts`) receive a zero vector in place of host features.

## Sampling

At training time, both heads sample from their distributions to maintain exploration:

```python
host_dist = Categorical(host_logits_masked)
host_slot = host_dist.sample()

action_input = concat(hidden, state[host_slot]) if conditioned else hidden
action_dist = Categorical(action_logits_masked(action_input, host_slot))
action = action_dist.sample()

log_prob = host_dist.log_prob(host_slot) + action_dist.log_prob(action)
entropy  = host_dist.entropy() + action_dist.entropy()
```

At evaluation time, argmax is used for both heads via `policy.predict()`.

## Interface

```python
policy = Policy(hidden_dim=128, num_layers=1, conditioned_action_head=False)

action, host_slot, log_prob, entropy = policy.sample(state_tensor, known_host_count)
action, host_slot                    = policy.predict(state_tensor, known_host_count)
```

## Files

- `rl/policy.py` — implementation
- `tests/test_policy.py` — unit tests (shapes, masking, log-prob, determinism, conditioned mode)
- `docs/adr/007-rl-algorithm-and-policy-design.md` — original design decisions
- `docs/adr/010-conditioned-action-head.md` — conditioned action head rationale
- `rl/actions.py` — action enum
- `rl/state.py` — state tensor structure
