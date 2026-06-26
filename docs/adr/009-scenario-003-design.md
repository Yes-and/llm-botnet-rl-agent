# ADR 009: Scenario-003 — Mixed-Target Environment Design

**Status:** Proposed (contended — not yet agreed)

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
