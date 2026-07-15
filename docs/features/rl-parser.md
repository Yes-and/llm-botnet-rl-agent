# RL Output Parser

**Status:** Implemented

## Overview

`rl/parser.py` parses attacker-side tool output and translates it into state feature updates and optional exploit events. It is the bridge between raw command output and the structured `EpisodeState`.

## Interface

```python
parse_step(command: str, output: str, exit_code: int) -> ParseResult
```

`ParseResult` contains:
- `state_updates` — list of `(ip, {feature: value})` pairs to apply to `EpisodeState`
- `exploit` — an `ExploitEvent` if exploitation was detected, otherwise `None`

The caller (environment) is responsible for deduplicating `ExploitEvent`s — the parser fires one whenever it detects success regardless of prior state. The `EpisodeState` already tracks whether `shell_access` is set, so the environment can gate reward on first-time access only.

## Dispatch

`parse_step` dispatches to a per-tool sub-parser based on the command string:

| Trigger | Sub-parser | Detects |
|---|---|---|
| `nmap` in command | `_parse_nmap` | host liveness, open ports, services |
| `hydra` in command | `_parse_hydra` | credential finds |
| `redis-cli` in command | `_parse_redis_cli` | unauthenticated Redis access |
| `MongoClient` in command | `_parse_mongo` | unauthenticated MongoDB access |
| `FTP(` or `ftplib` in command | `_parse_ftp_pylib` | FTP anonymous login (python3) |
| command starts with `ftp` | `_parse_ftp_bin` | FTP anonymous login (ftp binary) |
| `telnetlib` in command | `_parse_telnetlib` | Telnet service liveness |
| `sshpass` or `ssh` in command | `_parse_ssh` | SSH shell access |

## nmap

Handles both output formats. Partial output from timed-out scans is still parsed — exit code is ignored.

**Grepable format (`-oG -`):**
- `Host: <ip> (<hostname>) Status: Up` → sets `is_alive`, **unless `<hostname>` is empty**
- `Host: <ip> (<hostname>) Ports: <port>/open/tcp//<service>//...` → sets `port_*_open` and `service_*`, same exclusion

**Human-readable format (no `-oG`):**
- `Nmap scan report for [<hostname> (]<ip>[)]` + `Host is up` → sets `is_alive`, same exclusion

Port details are not extracted from human-readable output; use grepable format for port scanning steps.

**Docker infrastructure exclusion:** a host with no reverse-DNS hostname (empty parens in grepable format, or a bare IP with no hostname prefix in human-readable format) is treated as Docker network infrastructure — typically the bridge gateway, conventionally the subnet's `.1` address — rather than a real scenario container, and is dropped instead of being added to state. Real containers started by Compose always resolve to their service DNS name (e.g. `s003_host11.scenario-003_s003_net`); the gateway never does. This matters more under ADR 014 than it used to: previously a wasted step on the gateway was cheap (one `SCAN_NETWORK`-adjacent pick in a big multi-host episode); now, with single-host engagement, an unfilterable phantom host can eat up to `max_engagement_steps` per engagement and never leaves the pool (nothing ever exploits it), so it can be repeatedly re-selected across an entire episode. Found from a real smoke-test run where `172.21.0.1` (the gateway) consumed 7 of 20 total steps this way before the fix.

## hydra

Detects the standard credential-found line:
```
[22][ssh] host: 172.18.0.3   login: admin   password: admin123
```
Sets `creds_found` and the relevant `service_*` feature. Does not emit an `ExploitEvent` — shell access (not credential discovery) is the exploitation event.

As of ADR 014, `creds_found` also drives the policy's action mask directly (`rl/policy.py`'s `is_valid()`-based masking): once set, `BRUTE_FORCE_SSH`/`FTP`/`TELNET` mask out and the matching `CONNECT_*` action unmasks, so the next interaction step is structurally steered toward using the credentials rather than re-brute-forcing. (Pre-ADR-014, this same signal was instead used as an early-exit condition inside `Environment.step_block()`'s multi-try loop — that mechanism is retired; every interaction step is a single primitive command now, so there's no multi-try block to exit early from.)

## redis-cli

Exit code 0 + `redis_version:` present in output → `shell_access` + `ExploitEvent("redis_no_auth")`. The `redis_version:` marker only appears in a successful `INFO` response from an unauthenticated server — an auth-required server returns `NOAUTH Authentication required.` instead, and connectivity-only commands like `PING` (`PONG`) don't prove auth bypass. `KEYS`/`CONFIG GET` output alone is not treated as exploitation for the same reason.

## pymongo

Exit code 0 + no `Traceback`/`ServerSelectionTimeoutError` → `shell_access` + `ExploitEvent("mongodb_no_auth")`.

## FTP

Two sub-parsers handle the python3 ftplib and ftp binary cases separately, since each fails differently on unsuccessful login:
- **ftplib** (`_parse_ftp_pylib`): exit code 0 + no `Traceback` → `shell_access` + `ExploitEvent("ftp_anonymous_login")`. ftplib raises on connection/login failure (`ConnectionRefusedError`, `error_perm`), which shows up as a Traceback; it doesn't print the `230` response to stdout, so that can't be checked directly.
- **ftp binary** (`_parse_ftp_bin`): exit code 0 + `230` present in output → `shell_access` + `ExploitEvent("ftp_anonymous_login")`. The binary exits 0 even on a refused/failed login, so the exit code alone can't be trusted — the `230` success response is required.

## telnetlib

Exit code 0 + `login:` in output → sets `service_telnet`, `port_23_open`, `is_alive` for the target IP (extracted from `Telnet('ip', ...)` in the command). Reading the login prompt alone is reconnaissance, not exploitation — no `ExploitEvent` yet. If `uid=` is also present (the agent ran a shell command after logging in) → additionally sets `shell_access` + `ExploitEvent("telnet_weak_credentials")`.

## SSH

Exit code 0 + `uid=` in output + no `Permission denied`/`Connection refused` → `shell_access` + `creds_found` + `ExploitEvent("ssh_weak_credentials")`. Requires the agent to run a non-interactive command (e.g., `'id'`) to confirm shell access.

## Extending the Parser

- **New pattern for existing tool**: add a test case to the relevant parametrize list in `tests/test_parser.py`, then adjust the regex or condition in `rl/parser.py`.
- **New tool**: add a new sub-parser function and a dispatch condition in `parse_step`.

## Open Issues

- **Parser coverage logging**: there is no mechanism to detect when a step produced useful output that the parser failed to recognise. Future work should log unmatched steps (exit 0, non-empty output, no state update) to a debug log so gaps can be found and fixed. See ADR 006.

## Files

- `rl/parser.py` — implementation
- `tests/test_parser.py` — table-driven tests, one case per real run example
