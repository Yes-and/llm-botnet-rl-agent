# Scenario 003 — Mixed-Target Triage Environment

**Status:** Implemented

## Overview

Twelve-host network scenario, built on top of scenario-002's five exploitable service types plus dead and hardened hosts, so the policy has to learn triage (which hosts are worth pursuing) rather than exploit everything it finds. Full design rationale, alternatives, and the target-mix history are in [ADR 009](../adr/009-scenario-003-design.md) — this doc covers how the RL loop (this repo's live design: free per-step `(host, action, duration)` selection, see `docs/features/rl-training.md`) interacts with it, not the topology itself.

## Host mix

Per ADR 009's final mix (12 hosts on `172.21.0.0/24`, `sandbox/compose/scenario-003.yml`):

| Class | Count | Hosts |
|---|---|---|
| Exploitable | 5 | `host01` (SSH), `host03` (Telnet), `host06` (FTP anonymous), `host08` (MongoDB no-auth), `host11` (Redis no-auth) |
| Hardened | 5 | `host02` (Redis, `requirepass`), `host05` (MongoDB, auth required), `host07` (SSH, strong password), `host10` (Telnet, strong password), `host12` (FTP, anonymous disabled) |
| Dead | 2 | `host04`, `host09` — ping-visible, no listening services |

Same generic-hostname rule as scenario-002 (ADR 013): the agent discovers service type by scanning, not by reading it off DNS.

## Why triage matters here, specifically

With free per-step `(host, action, duration)` selection, every step is an independent choice of which host to engage and what to try against it — there's no structural mechanism forcing the policy to stay on or abandon a host, unlike a single-host-engagement design would have. That makes the three host classes a direct read on what the policy has learned:

- **Dead hosts** should get at most one `SCAN_PORTS`/`PROBE_PORT` attempt each before the policy stops re-selecting them — the state features (`is_alive=1`, no `port_*_open`) after that first probe are enough to condition the action/host heads away from further engagement, if triage has been learned.
- **Hardened hosts** are the expensive failure mode: the policy can spend budget on `BRUTE_FORCE_*` against `host07`/`host10` that will never crack (wordlist doesn't contain the password) or `CONNECT_FTP`/service auth against `host02`/`host05`/`host12` that's structurally blocked. Only the `-0.1` step penalty (no explicit "this is hardened" signal) is available to teach avoidance.
- **Exploitable hosts** behave exactly as in scenario-002 — same five services, same `_ACTION_VULNERABILITY` credit-assignment rules from `rl/environment.py`.

## Config

`experiments/configs/s003-*.yml` follow the same fields as scenario-002's configs (see `docs/features/rl-training.md`), with `max_steps` typically higher (ADR 009: 80) to give the free-selection policy enough steps to both discover and triage across 12 hosts rather than 5. `s003-case-*.yml`/`s003-*-hardened.yml` configs additionally target the hardened-only host subset for single-LLM case-study runs (`scripts/run_case_study.py`), independent of the RL training track.

## Open questions (from ADR 009, still open)

- Does the policy learn to skip dead hosts after one failed port scan?
- Does the policy learn to deprioritize hardened hosts after a failed brute-force/connect attempt?
- Does a policy trained on scenario-002 transfer to scenario-003 with minimal additional training?

ADR 009's "Update (2026-07-07)" section traces earlier non-triage findings to the multi-command chain problem (recon → brute-force → connect rarely completing by coincidence under single-command-per-step), addressed by the duration head (ADR 011) — restored as part of this repository's live design (ADR 019). Whether triage itself now emerges with duration enabled is still an open empirical question, not yet answered by a full training run.

## Files

- `sandbox/compose/scenario-003.yml`, `sandbox/compose/scenario-003-hardened.yml`
- `sandbox/images/ssh-hardened/`, `sandbox/images/ftp-hardened/`, `sandbox/images/telnet-hardened/`, `sandbox/images/telnet-target-hardened/` — hardened-variant Dockerfiles
- `docs/adr/009-scenario-003-design.md` — topology and design rationale
- `docs/adr/010-conditioned-action-head.md`, `docs/adr/011-action-duration-head.md` — the two policy changes ADR 009 traces triage attempts through
