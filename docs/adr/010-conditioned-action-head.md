# ADR 010: Conditioned Action Head — Triage-Aware Policy Architecture

**Status:** Accepted; partially superseded by ADR 011

**Supersedes:** action head parallelism decision in ADR 007.

## Context

ADR 007 established a policy with two parallel heads sharing a trunk MLP. Both heads receive only the trunk output `h = f_trunk(state)`. The action head therefore computes a distribution over actions that is independent of which specific host was selected:

```
π(host, action | state) = π_host(host | state) · π_action(action | state)
```

This independence assumption was acceptable for scenario-002, where all hosts are exploitable and any action targeted at any host is potentially useful. In scenario-003 (mixed targets: 5 exploitable, 5 hardened, 2 dead), it becomes a structural limitation.

Training results for MiniMax-M2.5 on scenario-003 confirmed the problem: the policy converged on `PROBE_REDIS` as a globally high-value action and applied it to every host, including the hardened Redis instance, throughout late episodes. The action head had no mechanism to reason "this specific host has `tried_probe_redis=1` and `shell_access=0` — do not probe it again." Triage did not emerge.

## Decision

Condition the action head on the selected host's feature vector. After sampling `host_slot`, extract the corresponding row from the state tensor and concatenate it to the trunk output before computing action logits:

```
π(host, action | state) = π_host(host | state) · π_action(action | host, state)
```

Concretely:

```
h = f_trunk(state)                          # shared trunk, shape [hidden_dim]
host_slot ~ Categorical(softmax(W_h · h))   # host head, unchanged

x_host = state[host_slot - 2]               # selected host features, shape [NUM_FEATURES]
                                             # (zero vector for broadcast slots 0 and 1)

action ~ Categorical(softmax(W_a · [h ∥ x_host]))   # conditioned action head
```

The action head's input dimension grows from `hidden_dim` to `hidden_dim + NUM_FEATURES`. Broadcast slots (`no_host` and `all_hosts`) receive a zero vector in the host feature position, keeping input dimensions constant.

## Why REINFORCE remains valid

The combined log-probability used in the policy gradient update is:

```
log π(host, action | state) = log π_host(host | state) + log π_action(action | host, state)
```

This is the chain rule decomposition of the joint — no approximation. Gradients flow through both terms normally. The host sampling step is handled by the log-derivative trick as before; no gradient needs to pass through the discrete host sample itself.

## Implementation

- `conditioned_action_head: bool = False` parameter on `Policy.__init__` — defaults to `False` for backward compatibility with all existing configs and checkpoints.
- `Policy._action_input(hidden, host_slot, state)` — computes the action head input.
- Existing parallel-head behaviour is unchanged when `conditioned_action_head=False`.
- Controlled via the experiment config YAML so the architecture choice is recorded alongside results.

## Alternatives considered

**Attention mechanism over host slots:** Replace the flat MLP trunk with a transformer that attends over host feature rows. Would give the host head per-slot attention and fix the same root problem more generally. Rejected as premature — adds significant complexity before we know whether the simpler conditioning is sufficient.

**Feature-based hard masking:** Prevent actions whose preconditions are not met (e.g. block `PROBE_REDIS` if `tried_probe_redis=1`). Rejected per ADR 007 — suppresses learning signal and may mask useful exploration.

## Expected outcome

The policy should now be able to learn per-host action suppression: if `tried_probe_redis=1` and `shell_access=0` appear in the selected host's feature vector, the action head can assign low probability to `PROBE_REDIS` for that host. Triage for hardened hosts should emerge more readily than with parallel heads.

## Superseded sections in ADR 007

| Section | What changed |
|---|---|
| "Parallel heads" | Action head is now optionally conditioned on selected host features via `conditioned_action_head` config flag |
| Joint distribution | `π(h, a | s) = π_host · π_action` replaced by `π_host · π_action(· | host, s)` when conditioned |
