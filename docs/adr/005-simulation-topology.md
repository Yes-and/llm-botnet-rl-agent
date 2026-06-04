# ADR 005 — Simulation Topology

## Decision

Incremental topology: start with a small flat network of 5–10 targets, expand after successful experimentation. One Docker Compose file per scenario. Each scenario includes an observer container alongside attacker and target nodes.

## Topology Structure

```
┌─────────────────────────── internal network ───────────────────────────┐
│                                                                         │
│   attacker  ──────────────────────────────────────  target-a            │
│       │                                             target-b            │
│       │                                             target-c  ...       │
│       │                                                                 │
│   observer  (read-only sidecar, not visible to agent)                  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

All nodes share a single flat internal network. No external egress.

## Key Decisions

**Incremental expansion.** Begin with 5–10 machines covering a small set of known vulnerabilities. Add more targets, hardened machines, and realistic traffic patterns only after the agent loop, reward signal, and observer are proven stable.

**One Compose file per scenario.** Each scenario is a self-contained stack with its own network and no shared volumes. Allows independent experimentation, easy reset, and safe parallel runs with distinct project names.

**Flat network now; routing later.** The current flat topology keeps the environment simple during early development. A gateway/router service will be introduced in a future scenario to enable subnet traversal and pivot-based attacks. No placeholder is added now — it would be dead weight until the RL formulation supports it.

**Observer container.** Each Compose file will include an observer service. It shares the internal network, has read access to target state (e.g., mounted log volumes or a shared socket), and is not reachable by the agent. Responsibilities: win condition detection, episode logging, and emitting reward signals to the RL environment wrapper. Keeping this logic outside the agent container ensures reward cannot be gamed and decouples observability from attack tooling. The observer is not yet implemented — reward signal design is now complete (see ADR 006) and observer implementation is the next unblocked step.

**Attacker starts blind.** The attacker container is given no prior knowledge of targets — no IP list, no hostnames (beyond DNS resolution on the internal network). Network scanning is the expected first action, and reward design should reflect this.

## Rationale

A small, flat, well-observed network is the fastest path to a working RL training loop. Complexity (routing, realistic traffic, large target pools) is deferred until the simpler version produces meaningful signal. Each expansion is a new scenario, preserving prior scenarios as regression baselines.

## Future Scenarios

- **Routing / subnet traversal** — add a gateway service; agent must pivot through a compromised node to reach an isolated subnet.
- **Larger target pools** — sample from vulhub or DVWA images for broader vulnerability coverage.
- **Hardened machines** — introduce targets that resist or detect scanning, adding a cost to noisy recon.
- **Realistic background traffic** — simulate benign traffic to make the environment less artificial.
