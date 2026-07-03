# ADR 009: Scenario-003 — Mixed-Target Environment Design

**Status:** Accepted

## Context

Scenario-002 has five targets, all exploitable. The policy only needs to learn *how* to exploit — it has no incentive to be selective, because every discovered host is a valid target.

This is unrealistic and limits what we can measure. A real attacker operates in environments where most hosts are either dead (no listening services) or hardened (services present but not exploitable with available tools). The policy should learn to efficiently identify and prioritise genuinely vulnerable hosts rather than blindly attempting every action on every discovered host.

A harder scenario also provides a stronger benchmark: success in scenario-003 requires the policy to learn *triage*, not just exploitation.

## Decision

Build scenario-003 as a new, independent Compose topology with three host classes:

**Exploitable hosts (carry over from scenario-002)**
The same five service types: SSH (weak credentials), FTP (anonymous login), Telnet (weak credentials), Redis (no auth), MongoDB (no auth). These are the only hosts that yield positive reward.

**Dead hosts**
Containers that respond to ICMP ping (visible to nmap `-sn`) but have no listening services. A port scan returns nothing. The policy should learn to skip these after a failed `SCAN_PORTS` or `PROBE_PORT` step.

**Hardened hosts**
Services are running and visible on the expected ports, but are not exploitable with the current tool set:
- SSH with a strong password (brute-force wordlist will not crack it)
- FTP requiring credentials (anonymous login rejected)

Hardened hosts are the most expensive mistake: the policy may spend multiple steps attempting brute-force before giving up. The reward signal for failed attempts (step penalty only) should eventually teach the policy to deprioritise these.

**Target mix (provisional)**

| Class | Count |
|---|---|
| Exploitable | 5 |
| Dead | 3 |
| Hardened | 2 |

Total: 10 discovered hosts (plus the attacker and gateway = 12 on the subnet). The exact mix can be adjusted after initial training runs.

## Research questions this enables

1. Does the policy learn to skip dead hosts after a single failed port scan?
2. Does the policy learn to deprioritise hardened hosts after a failed brute-force?
3. How many more episodes does scenario-003 require to reach the same exploit count as scenario-002?
4. Does a policy trained on scenario-002 transfer to scenario-003 with minimal additional training?

## Implementation notes

- New Compose file: `sandbox/compose/scenario-003.yml`. Networks must use `internal: true` per sandbox rules.
- Dead hosts: any minimal container image (e.g. `alpine` with `ping` responder only, or the existing `base` image with no service started).
- Hardened SSH: use the existing SSH image with a password not in the wordlist (`/usr/share/wordlists/rockyou.txt` or whatever the brute-force config uses).
- Hardened FTP: vsftpd with `anonymous_enable=NO` and a strong password for the only user.
- Reward function: no changes. Hardened hosts naturally produce only step penalties, which is the correct signal.
- New experiment config: `experiments/configs/s003-train-001.yml` following the same conventions as scenario-002.

## Alternatives Considered

**Extend scenario-002:** Add dead/hardened hosts to the existing Compose file. Rejected — violates the "keep scenarios independent" rule in CLAUDE.md and makes scenario-002 results incomparable with the baseline.

**Parametric difficulty (config-driven host mix):** A single Compose template with configurable host counts and hardening levels. Rejected as premature — adds significant complexity before we know what mix is useful for training.

**Rate-limited services as "hardening":** Add SSH rate limiting or account lockout instead of strong passwords. Rejected — interacts poorly with the 60s command timeout and makes the environment non-deterministic in ways that complicate reward attribution.

## Known Constraints (appended 2026-07-03)

**Final host mix:** 5 exploitable + 5 hardened (one mirror of each service type) + 2 dead = 12 targets. Episode length: 80 steps. The original Decision section's provisional mix (2 hardened, 3 dead) is superseded by this.

**Implementation status:** Compose file created at `sandbox/compose/scenario-003.yml` (subnet 172.21.0.0/24). New Dockerfiles created for `ssh-hardened`, `ftp-hardened`, `telnet-hardened`. Hardened Redis uses `--requirepass` via compose command; hardened MongoDB uses `MONGO_INITDB_ROOT_USERNAME/PASSWORD` env vars on the official image — no custom Dockerfiles needed for those two.

**Policy architecture limitation — parallel heads:** The current `Policy` network uses a host head and action head that share a trunk but are otherwise independent. The action head does not receive the selected host's features as input. This means the policy cannot tightly reason "pick host X AND pick the action appropriate for host X's specific state." It can learn global patterns (e.g. "probe_redis is high-value") and per-slot suppression (e.g. "slot 3 has tried_probe_redis=1 and shell_access=0, avoid it"), but the coupling between host selection and action selection is weak.

In scenario-003, this is a meaningful constraint: the policy may learn that a given action is globally high-value but still waste steps applying it to hardened hosts. Triage for dead hosts (clean port scan signal) should emerge more easily than triage for hardened hosts (requires correlating tried_* + shell_access=0 across the flattened input).

If triage fails to emerge after a reasonable number of episodes, the fix is to condition the action head on the selected host's feature vector. That change is deferred until training results are available.
