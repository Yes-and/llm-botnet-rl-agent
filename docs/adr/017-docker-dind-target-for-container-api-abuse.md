# ADR 017: Docker-in-Docker Target for Container-API Abuse Scenario

**Status:** Accepted

## Context

The last unbuilt entry on the real-botnet-campaign shortlist (see [[project_botnet_campaign_research]] in memory) is exposed Docker/Kubernetes API abuse — the TeamTNT/Graboid campaigns (Tier 2, Unit 42/Aqua/Trend Micro), which scan for exposed Docker daemon REST APIs (ports 2375/2376/2377/4243/4244) and abuse them to spin up privileged containers, mount the host filesystem, and achieve host-level compromise.

Unlike every prior scenario (Telnet/Mongo/Redis), this is the first target where a careless implementation could plausibly affect infrastructure *outside* the intended sandbox. The naive way to give a target container "a real Docker API to expose" is mounting the host's `/var/run/docker.sock` into it — but that socket **is** the real host Docker daemon's control interface. A container with it mounted (or anything that can reach through it) can create new containers with arbitrary host-filesystem mounts, which is a well-known real-world privilege-escalation technique in its own right. Doing this in a target container the LLM agent is actively trying to exploit would mean a successful exploit reaches the user's actual machine, not a contained simulation — unacceptable given every other scenario in this project guarantees containment via `internal: true` networking and per-scenario isolation.

## Decision

Use **Docker-in-Docker** (`docker:dind`) as the target's base image for the new scenario (scenario-007). `dind` runs its own separate, nested Docker daemon inside the container, with its own storage and container namespace, entirely disconnected from the host's real daemon. The attacker container exploits *this* nested daemon's exposed API over the scenario's own `internal: true` network — identical containment guarantee to every other scenario. No host socket or host filesystem path is ever mounted into any scenario-007 container.

`docker:dind` requires the target container to run with `--privileged` to create its own nested namespaces/cgroups. Per CLAUDE.md's sandbox rule ("Avoid elevated container privileges. If `--privileged` is ever needed, document the reason explicitly"), this is that documentation: `--privileged` here grants broader access *within that one container's own namespace* (device access, capability set) — it does **not** expose the host's real Docker socket, host filesystem, or host network. The blast radius stays the same as every other scenario: whatever the LLM does inside the target's nested daemon only ever affects that nested daemon's own throwaway containers.

## Alternatives considered

**Mount the host's `/var/run/docker.sock` into the target.** Rejected outright — this is the exact real-world escalation vector being simulated, and doing it for real here would let a successful exploit reach the user's actual Docker environment. Never on the table.

**`docker:dind-rootless`** — avoids `--privileged` in many configurations by running the nested daemon as a non-root user. Considered as a lower-privilege alternative, but rootless dind has real, documented feature gaps and setup fragility (particularly around networking and storage drivers) for a benefit that doesn't actually change the containment guarantee — classic dind already fully prevents host access regardless of privileged/rootless, since that guarantee comes from *not mounting the host socket*, not from the privilege level. Not worth the added fragility preemptively; revisit only if classic dind proves genuinely too permissive for some concrete reason once built.

**Don't build this scenario.** Considered — it's the biggest lift of the remaining shortlist items (new scenario from scratch, no existing hooks, unlike Redis which reused scenario-003's existing no-auth pattern). Rejected because it's the last uncovered real campaign class (Telnet/Mongo/Redis all now covered) and completes the shortlist.

## Consequences

- New `sandbox/compose/scenario-007.yml` and a dedicated target image/config, not yet built as of this ADR — this document records the containment decision made *before* implementation, per the user's explicit request given the real stakes involved.
- The target container carries `--privileged`, the first scenario in this project to need it — every other scenario's `cap_drop: ALL`/`no-new-privileges` convention on the *attacker* side is unaffected and unchanged; this is scoped to the *target* only.
- Whatever exploit-detection/win-condition mechanism gets built for this scenario needs to verify compromise *within the nested daemon* (e.g., a container created inside it with a mounted path, proving container-escape-equivalent access) — not a real host filesystem check, since there is no real host access to check.
- Not yet cited to a specific documentation tier for the exact technique shape (Graboid's host-infection counts were already flagged in the original campaign research as needing a primary-source check before citing — still applies).
