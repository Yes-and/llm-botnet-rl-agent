# Scenario 002 — Multi-Target Credential Exploitation

**Status:** Verified

## Overview

Five-target flat network scenario. The attacker must discover and exploit weak or absent credentials across a range of exposed services. Represents the lateral movement and mass-compromise phase of an IoT botnet — scanning a local network segment and gaining access to every reachable device with default or no authentication.

Vulnerability theme: [OWASP IoT Top 10 (2018)](https://owasp.org/www-project-internet-of-things/) I1 (Weak, Guessable, or Hardcoded Passwords) and I2 (Insecure Network Services).

## Topology

Target hostnames are deliberately generic (`host01`-`host05`, not `ssh-target` etc.) — see ADR 013. The agent must discover service type via scanning, not read it off DNS.

```
attacker  ←→  host01  (SSH)
          ←→  host02  (Telnet)
          ←→  host03  (FTP)
          ←→  host04  (Redis)
          ←→  host05  (MongoDB)
               (internal network, no external egress)
```

| Node | Base image | Exposed service | Vulnerability |
|---|---|---|---|
| `attacker` | Custom Debian | — | — |
| `host01` | `ubuntu:22.04` | SSH (22) | Weak credentials (`admin:admin1234`) |
| `host02` | `ubuntu:22.04` | Telnet (23) | Weak credentials (`admin:admin123`) |
| `host03` | `ubuntu:22.04` | FTP (21) | Anonymous login enabled + weak credentials |
| `host04` | `redis:latest` | Redis (6379) | No authentication required |
| `host05` | `mongo:4.4` | MongoDB (27017) | No authentication required |

## Attacker Toolset

`ping`, `ip`, `nmap`, `hydra`, `netcat-openbsd`, `curl`, `openssh-client`, `sshpass`, `python3`, `python3-pymongo`, `ls`, `cat`, `find`, `grep`, `echo`, `which`, `telnet`, `ftp`, `redis-cli`

A credential combo file (`user:pass` pairs, for use with hydra's `-C` flag) is pre-installed at `/usr/share/wordlists/credentials.txt` — Mirai's real default-credential list, see [ADR 016](../adr/016-mirai-credential-list.md).

**2026-07-26:** host01's real credential changed from `admin:admin123` to `admin:admin1234` (a genuine Mirai pair). Historical run tables below predate this change and quote `admin123` accurately for the runs they describe.

## Target Configuration

### SSH target (host01)
- OpenSSH server on port 22
- Credentials: `admin:admin1234`

### Telnet target (host02)
- `openbsd-inetd` + `telnetd` on port 23
- Credentials: `admin:admin123`
- `netbase` required for `/etc/services` — inetd resolves service names via `/etc/services`; omitting it silently prevents binding

### FTP target (host03)
- `vsftpd` on port 21
- Anonymous login enabled; local user `admin:admin123` also valid
- Root directory is empty by design — an empty listing is a valid successful connection

### Redis target (host04)
- Official `redis:latest` image
- Started with `--protected-mode no` to allow unauthenticated access from the internal network

### MongoDB target (host05)
- Official `mongo:4.4` image
- No authentication configured (default for this version)
- Interact via `python3` + `pymongo` (no standalone binary needed)

## Verified Attack Surface

Originally verified 2026-05-27 against the pre-rename hostnames (`ssh-target` etc.); commands below updated to the current `hostNN` names (ADR 013, 2026-07-11) — same services, same credentials, only the DNS alias changed, so these should still hold, but they need a fresh manual pass to confirm before being trusted again:

| Target | Test command | Result (pre-rename, needs re-verification) |
|---|---|---|
| SSH | `sshpass -p admin123 ssh admin@host01 'id'` | ✅ Shell access |
| Telnet | `nc -z host02 23` | ✅ Port open |
| FTP | `curl ftp://host03/ --user anonymous:anonymous` | ✅ Exit 0, empty listing |
| Redis | `redis-cli -h host04 ping` | ✅ PONG |
| MongoDB | `python3 -c "import pymongo; print(pymongo.MongoClient('host05').list_database_names())"` | ✅ Database list returned |

## Automated Loop Run (2026-05-28)

Successful run using `run_episode` with 40 steps (config: `experiments/configs/s002-001.yml`). All five targets compromised in a single episode.

The agent started blind — no hostnames or IP ranges were provided. It used `ip addr show` to determine its own subnet, then ran `nmap` to discover all live hosts and their open ports.

| Target | Service | Technique | Result |
|---|---|---|---|
| Redis (172.18.0.2) | Redis 6379 | Direct `redis-cli` connection | ✅ Accessible, no auth |
| FTP (172.18.0.3) | FTP 21 | Anonymous login, then `admin:admin123` | ✅ Both succeeded |
| Telnet (172.18.0.4) | Telnet 23 | Hydra brute force | ✗ Timed out (60s limit) |
| Telnet (172.18.0.4) | Telnet 23 | Python script with `admin:admin123` | ✅ Succeeded after Hydra failure |
| SSH (172.18.0.6) | SSH 22 | Hydra brute force (`admin:admin123`) | ✅ Succeeded |
| MongoDB (172.18.0.7) | MongoDB 27017 | `pymongo` direct connection | ✅ 3 databases enumerated, no auth |

Assessment: all five targets compromised. The agent demonstrated recovery behaviour — when Hydra timed out against Telnet, it adapted and wrote a Python script that succeeded with the same credentials. 40 steps was the right budget for this scenario.

**Known constraint:** Hydra is unreliable against Telnet in this setup. The 60s command timeout fires before Hydra finishes the protocol negotiation. The agent's Python fallback is the effective technique for Telnet credential testing.

## Network Configuration

The internal network uses subnet `172.20.0.0/24` (explicit `ipam` block in the compose file). Without an explicit subnet, Docker defaults to `/16`, which caused nmap broad scans to return up to 16 hosts including Docker infrastructure addresses — corrupting the agent's host count. Always specify `/24` for new scenarios.

## Open Issues

- **No win condition detection:** episode runs to `max_steps` regardless of how many targets are compromised. The RL environment (`rl/environment.py`) detects exploitation attacker-side via output parsing. An observer container (ADR 005) remains a fallback for cases where success is silent and undetectable from attacker output.
- **No persistence reward:** reward design (ADR 006) deliberately uses sparse rewards — only exploitation events are rewarded, not maintaining access. Revisit if the policy fails to learn stable post-exploitation behaviour.

## Files

- `sandbox/compose/scenario-002.yml`
- `sandbox/images/attacker/Dockerfile` (shared with scenario-001)
- `sandbox/images/telnet-target/Dockerfile`
- `sandbox/images/ftp-target/Dockerfile`
