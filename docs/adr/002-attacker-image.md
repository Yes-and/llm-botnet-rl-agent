# ADR 002 — Attacker Container Image

## Decision

Use a custom Debian-based image for the attacker node rather than the official Kali Linux Docker image.

Included tools: `nmap`, `hydra`, `netcat-openbsd`, `curl`, `openssh-client`, `sshpass`, `python3`, `python3-pymongo`, `iputils-ping`, `telnet`, `ftp`, `redis-tools`.

## Rationale

The Kali image is ~5GB and ships hundreds of tools that will go unused. A purpose-built image is:

- **Smaller and faster** to pull and rebuild during experiments.
- **Explicit about the action space** — the tools available to the agent are defined in the Dockerfile, not inherited from a distribution. This matters for reproducibility and for reasoning about what the agent can do.
- **Reproducible** — no surprise tool updates between runs.

Kali can be revisited if a future scenario requires tools not easily packaged manually.