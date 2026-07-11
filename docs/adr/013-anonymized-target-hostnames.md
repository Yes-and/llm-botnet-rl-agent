# ADR 013: Anonymized Target Hostnames

**Status:** Accepted

## Context

Target containers in `sandbox/compose/scenario-002.yml` and `scenario-003.yml` used self-describing `hostname:`/`container_name:` values (`ssh-target`, `redis-hardened`, `s003_ssh_hard`, etc.). Docker Compose's embedded DNS on a user-defined bridge network resolves all three of a service's identifiers — the YAML service key, the explicit `hostname:`, and `container_name:` — as aliases, and `nmap`'s default reverse-DNS lookup surfaces them during ordinary subnet scanning.

This was a real, confirmed leak, not a theoretical one: a real `s003-train-minimax-m27-conditioned-004` training run's own text response to a blind subnet scan read:

> Notable services visible from hostnames:
> - **Telnet**: 172.21.0.3, 172.21.0.9
> - **SSH**: 172.21.0.5, 172.21.0.8
> ...

and its host table listed entries as `s003_ssh_hard`, `s003_redis_hard`, `s003_telnet_hard` — the model read both service type *and* hardened-status directly off hostnames, before any exploitation attempt. `ADR 009`/`010`/`011` exist specifically to help the policy learn to distinguish hardened from soft targets through trial and reward; if the hostname already answers that question, there is nothing to learn, and every triage-related result measured so far (including the persistent `PROBE_REDIS`-on-hardened-hosts pattern noted in ADR 012's context) sits on top of this confound.

## Decision

Rename every target's `hostname:`, `container_name:`, and YAML service key to a generic `hostNN` identifier, in both `scenario-002.yml` and `scenario-003.yml`. The attacker's own container name is unaffected (`s002_attacker`/`s003_attacker` — not itself a target, no leak concern).

Numbering is deliberately **shuffled independent of service type and hardened-status** (e.g. `host02` is a hardened Redis, `host11` is a soft Redis, `host04`/`host09` are the dead hosts) — sequential-by-tier numbering (e.g. hardened hosts always 06-10) would just relocate the leak from the name to the number.

`experiments/configs/s003-case-*.yml` (single-target capability case studies, see `docs/features/agent-loop.md`) were updated to reference the new hostnames, since those configs intentionally tell the LLM the target hostname in the task text — that's a separate, deliberate simplification (testing execution capability given a known target, not discovery capability) unaffected by this decision.

## Alternatives considered

**Random word-based names** (e.g. Docker's own adjective-noun generator). Rejected — no real advantage over sequential numbers for this purpose, and a generated word carries a small risk of accidentally suggesting something (a wordlist entry, a real service name) that a large model's training data associates with a specific meaning. Plain numbers carry no semantic content at all.

**Anonymize only the hardened/soft suffix, keep the service name** (e.g. `redis01`, `redis02` instead of fully generic). Rejected — service type is exactly the kind of thing port-scanning is supposed to reveal; leaving it in the hostname would still shortcut the SCAN_PORTS/PROBE_* actions' purpose, just not the triage question specifically.

## Consequences

- `docs/features/scenario-002-multi-target.md` updated (topology, tables, verified-command examples) to match. No `docs/features/scenario-003-*.md` exists — scenario-003's design lives entirely in ADR 009, which is append-only; this ADR is the record of the change instead.
- `sandbox_credentials` memory updated to reference the new `hostNN` names.
- Previously-generated training run logs (gitignored, not rewritten) still reference the old descriptive names — this only changes the environment for runs going forward, not historical data. Any analysis reading an old log needs to know the mapping was different at that time.
- IPs were already documented as unstable across runs (assigned by container-creation order); hostnames are now the stable, low-information identifier instead — the intended replacement for "which IP is which" bookkeeping.
- **Smoke tested 2026-07-11, both scenarios.** `docker compose down` alone left old-named target containers running (orphaned relative to the renamed services — needed `--remove-orphans`). After that, confirmed clean on scenario-003: 13 containers with new `hostNN` names, `nmap` resolves `host01` and finds port 22 open with rDNS now returning only `s003_host01` (no service/tier info), `sshpass -p admin123 ssh admin@host01 id` returns a real `uid=1000(admin)...` (full exploit path intact), `redis-cli -h host11 ping` returns `PONG`. Scenario-002 confirmed the same way: 6 containers with new names, `nmap`/rDNS clean, `sshpass ... id` returns real `uid=1000(admin)...`, `redis-cli -h host04 ping` returns `PONG`.
