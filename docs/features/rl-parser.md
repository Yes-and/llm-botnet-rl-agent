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
- `Host: <ip> (...) Status: Up` → sets `is_alive`
- `Host: <ip> (...) Ports: <port>/open/tcp//<service>//...` → sets `port_*_open` and `service_*`

**Human-readable format (no `-oG`):**
- `Nmap scan report for <ip>` + `Host is up` → sets `is_alive`

Port details are not extracted from human-readable output; use grepable format for port scanning steps.

## hydra

Detects the standard credential-found line:
```
[22][ssh] host: 172.18.0.3   login: admin   password: admin123
```
Sets `creds_found` and the relevant `service_*` feature. Does not emit an `ExploitEvent` — shell access (not credential discovery) is the exploitation event.

## redis-cli

Exit code 0 + output not starting with `ERR`/`WRONGTYPE`/`NOAUTH`/`DENIED` → `shell_access` + `ExploitEvent("redis_no_auth")`. Empty output (empty database) is treated as success.

## pymongo

Exit code 0 + no `Traceback`/`ServerSelectionTimeoutError` → `shell_access` + `ExploitEvent("mongodb_no_auth")`.

## FTP

Two sub-parsers handle the python3 ftplib and ftp binary cases separately. Exit code 0 + no `Traceback` → `shell_access` + `ExploitEvent("ftp_anonymous_login")`.

## telnetlib

Exit code 0 + `login:` in output → sets `service_telnet`, `port_23_open`, `is_alive` for the target IP (extracted from `Telnet('ip', ...)` in the command). No `ExploitEvent` — reading the login prompt is reconnaissance, not exploitation.

## SSH

Exit code 0 + `uid=` in output + no `Permission denied`/`Connection refused` → `shell_access` + `ExploitEvent("ssh_weak_credentials")`. Requires the agent to run a non-interactive command (e.g., `'id'`) to confirm shell access.

## Extending the Parser

- **New pattern for existing tool**: add a test case to the relevant parametrize list in `tests/test_parser.py`, then adjust the regex or condition in `rl/parser.py`.
- **New tool**: add a new sub-parser function and a dispatch condition in `parse_step`.

## Open Issues

- **Parser coverage logging**: there is no mechanism to detect when a step produced useful output that the parser failed to recognise. Future work should log unmatched steps (exit 0, non-empty output, no state update) to a debug log so gaps can be found and fixed. See ADR 006.

## Files

- `rl/parser.py` — implementation
- `tests/test_parser.py` — table-driven tests, one case per real run example
