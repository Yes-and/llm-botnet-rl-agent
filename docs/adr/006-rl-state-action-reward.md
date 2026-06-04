# ADR 006 — RL State, Action, and Reward Representation

**Status: Adopted**

## Context

ADR 003 defines the high-level policy architecture: a learned policy network steers a frozen LLM via prompt guidance, trained with PPO. This ADR specifies the concrete representation of state, action, and reward that the policy network operates on. These decisions are one level below ADR 003 and complement it.

---

## State

### Perspective

State is attacker-side only. The agent knows exactly what its own tools have revealed and nothing more. This makes the problem a POMDP (partially observable MDP), which is more realistic and produces more meaningful benchmark results than a god-mode observer state.

### Structure

The state is a fixed-size matrix of shape `[MAX_HOSTS, F]` where `MAX_HOSTS = 16` (a hard cap covering all current and planned scenarios) and `F` is the number of per-host features. Undiscovered host slots are zero-padded.

Each row has two components:

**Knowledge vector** — what the attacker has learned about this host:

| Feature | Description |
|---|---|
| `is_alive` | Host responded to reachability probe |
| `port_21_open`, `port_22_open`, `port_23_open`, `port_80_open`, `port_443_open`, `port_6379_open`, `port_27017_open` | Known open ports |
| `service_ssh`, `service_telnet`, `service_http`, `service_ftp` | Identified services |
| `creds_found` | At least one valid credential pair discovered |
| `shell_access` | Interactive shell obtained |
| `is_root` | Shell has root/admin privileges |

**Coverage vector** — which actions have been attempted against this host (one bit per action type). Prevents the policy from re-trying actions that have already been exhausted.

| Bit | Action |
|---|---|
| 0 | `do_nothing` |
| 1 | `scan_network` |
| 2 | `scan_ports` |
| 3 | `probe_port` |
| 4 | `brute_force_ssh` |
| 5 | `brute_force_ftp` |
| 6 | `brute_force_telnet` |
| 7 | `connect_ssh` |
| 8 | `connect_ftp` |
| 9 | `connect_telnet` |
| 10 | `probe_http` |
| 11 | `probe_redis` |
| 12 | `probe_mongo` |

### Host Ordering

Hosts are sorted by IP address numerically before constructing the matrix. This gives a consistent, deterministic ordering across episodes without requiring the network to be permutation-invariant.

### Feature List Scope

Every feature corresponds to something a specific tool output parser can set. If nothing in the attacker toolset can discover a feature, it is not included. New tools added to the attacker image may introduce new features; this invalidates previously trained weights and constitutes a breaking change to the state representation.

---

## Action

### Factored Heads

The policy network has two output heads:

- **Action head** — a softmax distribution over 13 action types: `{do_nothing, scan_network, scan_ports, probe_port, brute_force_ssh, brute_force_ftp, brute_force_telnet, connect_ssh, connect_ftp, connect_telnet, probe_http, probe_redis, probe_mongo}`
- **Host head** — a softmax distribution over host slots 1–16 plus an `all/broadcast` option for actions that are not host-specific (e.g., network scan)

The argmax of each head is combined into a natural language instruction injected into the LLM prompt, e.g.: *"Attempt SSH on host 12 (10.0.0.12)"*.

During training, heads are sampled from their distributions (not argmax) to maintain exploration. Argmax is used at evaluation time.

### Action Masking

Before applying softmax, logits for invalid (action, host) combinations are masked to `-inf` based on the current state. Examples:

- `brute_force_credentials` is masked for any host without `creds_found = 0` and a known relevant service
- `attempt_ssh` is masked for any host without `port_22_open = 1`
- `scan_ports` is masked for any host with `is_alive = 0`

Masking prevents the policy from selecting actions that cannot succeed given current knowledge, improving sample efficiency.

Full mask conditions per action:

| Action | Host scope | Valid when |
|---|---|---|
| `do_nothing` | broadcast | always |
| `scan_network` | broadcast | always |
| `scan_ports` | per-host | `is_alive` |
| `probe_port` | per-host | `is_alive` |
| `brute_force_ssh` | per-host | `port_22_open` |
| `brute_force_ftp` | per-host | `port_21_open` |
| `brute_force_telnet` | per-host | `port_23_open` |
| `connect_ssh` | per-host | `creds_found` AND `service_ssh` |
| `connect_ftp` | per-host | `creds_found` AND `service_ftp` |
| `connect_telnet` | per-host | `creds_found` AND `service_telnet` |
| `probe_http` | per-host | `port_80_open` OR `port_443_open` |
| `probe_redis` | per-host | `port_6379_open` |
| `probe_mongo` | per-host | `port_27017_open` |

### Rejected Alternative: Autoregressive Action Selection

An autoregressive approach — sample action type first, then sample host conditioned on the chosen action — more naturally models the correlation between action and host without requiring explicit masking. It was rejected for early work due to the added complexity of gradient flow through a discrete intermediate sample (requires Gumbel-softmax or a REINFORCE estimator). Revisit if action masking proves insufficient.

---

## Reward

### Structure

| Event | Reward |
|---|---|
| Successful exploitation of a vulnerability | +10 |
| Any other step | −0.1 |
| Detection / destructive action (future) | large negative (TBD) |

The step penalty creates pressure toward efficiency without dominating the exploitation signal (worst case for a 40-step episode: −4, well below a single +10).

### Success Detection

Exploitation success is determined by parsing attacker-side tool output — shell banners, credential confirmation messages, etc. Each scenario defines its own success parser rules. This keeps detection consistent with the attacker-perspective state design.

An exploitation counter per (host, vulnerability) pair is maintained for debugging. It should always be 0 or 1 in normal operation; values above 1 indicate reward farming and a bug in the state/masking logic.

For scenarios where exploitation success is not detectable from attacker output, the observer container (see ADR 005) may be consulted as a fallback. This is an escape hatch, not the default path.

### Rejected Alternative: Novel Discovery Rewards

Intermediate rewards for first-time discoveries (new host, new open port, valid credentials) would provide denser signal during early training. Rejected in favour of sparser rewards to keep the reward structure simple and avoid shaping the policy toward reconnaissance over exploitation. Reinstate as a fallback if the policy fails to learn with sparse rewards alone.

---

## Relationship to ADR 003

ADR 003 describes *how* the policy network is structured and trained (embedding network, PPO, inference latency concerns). This ADR describes *what* the network operates on. Both are required for a complete specification of the RL formulation.
