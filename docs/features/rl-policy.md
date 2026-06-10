# RL Policy

**Status:** Planned

## Overview

`rl/policy.py` implements the policy network used during REINFORCE training. It maps the current episode state tensor to a distribution over `(host, action)` pairs, from which the training loop samples to produce the instruction injected into the LLM prompt.

See ADR 003 and ADR 007 for the full design rationale.

## Architecture

```
state [MAX_HOSTS, NUM_FEATURES]
    → flatten → [MAX_HOSTS * NUM_FEATURES]
    → shared MLP trunk
    → host head: softmax over [MAX_HOSTS + 2] slots
    → action head: softmax over [NUM_ACTIONS] (conditioned on sampled host)
```

The shared MLP trunk produces a single hidden representation used by both heads. The host head and action head are separate linear layers applied on top of it.

## Host Head

Outputs a distribution over `MAX_HOSTS + 2` slots:

| Index | Meaning |
|---|---|
| 0 | `no_host` — for `DO_NOTHING` and non-targeted actions |
| 1 | `all_hosts` — for `SCAN_NETWORK` and broadcast actions |
| 2 … MAX_HOSTS+1 | Discovered hosts, ordered by IP |

Host slots beyond `len(known_hosts())` are hard-masked to `-inf` before softmax.

## Action Head

Outputs a distribution over all 13 action types (see `rl/actions.py`), conditioned on the sampled host. Structurally invalid `(host, action)` combinations (e.g. `SCAN_NETWORK` with a specific host slot, or a per-host attack with `all_hosts`) are soft-masked: logits are pushed to a large negative value before softmax, keeping near-zero probability without hard exclusion.

No feature-based masking is applied. The policy is not prevented from attempting an action whose preconditions (e.g. `port_22_open`) have not been confirmed. The agent learns through reward signal whether recon-before-attack is beneficial.

## Sampling

At training time, both heads are sampled from their distributions (not argmax) to maintain exploration:

```python
host_dist = Categorical(host_logits_masked)
host_idx = host_dist.sample()

action_dist = Categorical(action_logits_masked(host_idx))
action = action_dist.sample()

log_prob = host_dist.log_prob(host_idx) + action_dist.log_prob(action)
```

At evaluation time, argmax is used for both heads.

## Interface

```python
policy = Policy(hidden_dim=128, num_layers=2)

action, host_idx, log_prob = policy.sample(state_tensor, known_host_count)
action, host_idx           = policy.predict(state_tensor, known_host_count)  # argmax
```

## Files

- `rl/policy.py` — implementation (planned)
- `docs/adr/007-rl-algorithm-and-policy-design.md` — design decisions
- `rl/actions.py` — action enum and `is_valid`
- `rl/state.py` — state tensor structure
