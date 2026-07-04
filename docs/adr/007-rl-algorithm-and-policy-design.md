# ADR 007 — RL Algorithm and Policy Design

**Status: Partially superseded by ADR 010**

**Supersedes:** algorithm section of ADR 003; action representation and masking section of ADR 006.

## Context

ADR 003 specified PPO as the RL algorithm and action-first factored heads. ADR 006 specified feature-based hard action masking. Both decisions were made before any training runs existed. After reviewing the problem constraints — sparse rewards, high inference latency per step, and the goal of benchmarking *learned* attacker behaviour rather than hand-engineered policy structure — simpler and more realistic choices are warranted.

---

## Decisions

### 1. Algorithm: REINFORCE instead of PPO

REINFORCE (Monte Carlo policy gradient) replaces PPO as the baseline algorithm.

**Rationale:** PPO adds complexity (critic network, value bootstrapping, clipping) that is not justified before a baseline exists. REINFORCE requires only a policy network, collects full episodes, and produces unbiased gradient estimates. With episode lengths of 40 steps and sparse rewards, the variance of REINFORCE is acceptable and can be reduced with a simple baseline if needed.

**Update rule:**

```
∇J = Σ_t G_t · ∇ log π(a_t, h_t | s_t)
```

where `G_t = Σ_{k=t}^{T} γ^{k-t} · r_k` is the discounted return from step t.

**Optional baseline:** subtract the mean return across the episode from each `G_t` to reduce variance. Applied as a first mitigation if training is unstable; does not introduce a learned critic.

**Rejected alternative:** PPO. Deferred — not a baseline until REINFORCE is benchmarked.

---

### 2. Sampling order: host-first

The policy samples the host first, then the action conditioned on the host:

```
π(h, a | s) = π(h | s) · π(a | s, h)
log π(h, a | s) = log π(h | s) + log π(a | s, h)
```

**Rationale:** "Pick a target, then decide what to do" reflects realistic attacker intent. It also makes the action-level mask natural: given a selected host, the action distribution is conditioned on what that host represents (no_host, all_hosts, or a specific discovered target).

**Rejected alternative:** action-first sampling (ADR 003/006). Reversed here.

---

### 3. Host head redesign

The host head is a softmax over `MAX_HOSTS + 2` slots:

| Slot | Meaning | Always available |
|---|---|---|
| `no_host` | No specific target — for `DO_NOTHING` and future non-targeted actions | Yes |
| `all_hosts` | Entire network — for `SCAN_NETWORK` and future broadcast actions | Yes |
| `host_0` … `host_{N-1}` | Discovered hosts, sorted by IP | Only if discovered |

`SCAN_NETWORK` is always paired with `all_hosts`. Per-host actions (`SCAN_PORTS`, `PROBE_PORT`, `BRUTE_FORCE_*`, `CONNECT_*`, `PROBE_*`) target specific host slots.

**Supersedes:** the host head in ADR 006, which used a flat `MAX_HOSTS + 1` (broadcast + slots 1–16) design without the `no_host` concept.

---

### 4. Masking: minimal hard + soft for invalid combinations

**Hard masking (forbidden):** host slots beyond `len(known_hosts())` are masked to `-inf` before softmax. These slots correspond to hosts that do not exist in the current episode state and cannot be acted on.

**Soft masking (near-zero probability):** structurally invalid `(host, action)` combinations — e.g. a per-host attack paired with `all_hosts`, or `SCAN_NETWORK` paired with a specific host slot — have their logits pushed to a large negative value (not `-inf`) before softmax. This keeps the gradient path open while making the combination effectively unreachable.

**No feature-based masking.** The policy is not prevented from attempting `BRUTE_FORCE_SSH` on a host without a confirmed open port 22. The agent must learn through reward signal that scanning before attacking is beneficial. Enforcing this order via masking would leak implicit oracle knowledge about the environment and prevent the policy from discovering shotgun strategies that may be effective in some scenarios.

**Rationale for soft over hard for invalid combinations:** hard masking of zero-probability invalid combinations can cause numerical issues and makes the policy brittle if the invalid set changes. Near-`-inf` logits achieve the same practical effect while keeping the network well-behaved.

**Supersedes:** feature-based hard masking in ADR 006.

---

### 5. Random-step reward assignment (training fallback)

If credit assignment is too weak with standard discounted returns — i.e. the policy fails to learn a useful signal from sparse +10 rewards — a fallback technique is available: assign the terminal episode reward to a single randomly sampled step and treat all other steps as having zero terminal contribution. This concentrates the gradient update rather than diffusing it across the full return sum, and has empirically helped in prior work with similar sparse-reward episodic settings.

This is not applied by default. It is a named fallback to try before adding intermediate rewards or switching to a more complex algorithm.

---

## Relationship to Prior ADRs

| Topic | Prior decision | This ADR |
|---|---|---|
| RL algorithm | PPO (ADR 003) | REINFORCE |
| Sampling order | Action-first (ADR 003/006) | Host-first |
| Host head slots | Broadcast + MAX_HOSTS (ADR 006) | no_host + all_hosts + MAX_HOSTS |
| Masking | Feature-based hard masking (ADR 006) | Minimal hard + soft for invalid combos only |
