# Command Executor

**Status:** Implemented

## Overview

The command executor sits between the LLM and the Docker container. It receives tool calls from the LLM, validates and executes them inside the attacker container, and returns the output. It is the primary safety boundary between the LLM's generated commands and the sandbox.

## Mitigations

### Pre-execution

| Mitigation | Detail |
|---|---|
| **Binary allowlist** | Only commands starting with an allowed binary are executed. Allowed set: `nmap`, `hydra`, `netcat`, `nc`, `curl`, `ssh`, `sshpass`, `ssh-keygen`, `python3`, `ping`, `ip`, `ls`, `cat`, `find`, `grep`, `echo`, `which`, `telnet`, `ftp`, `redis-cli`. Anything else is rejected; the rejection reason is returned to the LLM as the tool result so it can adjust. |
| **Dangerous pattern blocklist** | Secondary check for patterns that should never appear regardless of binary: `rm`, `dd`, `mkfs`, fork bomb syntax (`:(){ :|:& };:`), writes to `/dev/` (stdout only, see below), and (2026-07-31) `os.system(`/`subprocess.*(`/`os.popen(` — python3 shelling out to reach a binary not in the allowlist above. Each pattern carries a human-readable reason returned in the rejection, not the raw regex. |
| **`/dev/` redirect exemption for `2>/dev/null`** (2026-07-17, reverses the 2026-07-11/16 "left intentionally blocked" stance) | The `/dev/` pattern originally blocked *any* redirect there, including `2>/dev/null` — plain stderr suppression, not destructive. That was deliberately kept broad at first because the model's own hydra self-correction depends on seeing stderr (e.g. `[ERROR] File for passwords not found: ...`), and a rejected-then-retried-without-the-redirect command was the mechanism forcing that visibility. But it was also confirmed rejecting the model's *correct* filesystem self-discovery (`find /usr/share/wordlists -name "*.txt" 2>/dev/null`, `ls ... 2>/dev/null`) — punishing exactly the behavior the wordlist-hallucination measurement wants to see more of. Narrowed to `(?<!2)>\s*/dev/`: `2>/dev/null` now executes directly, `>/dev/null`/`1>/dev/null`/`&>/dev/null` (stdout-hiding) still rejected. **Known open risk, not yet confirmed in a live run:** a hallucinated-wordlist `hydra` call piped through `2>/dev/null` (seen in real transcripts, e.g. `hydra -l admin -P .../rockyou.txt ssh://... 2>/dev/null`) now executes with hydra's own diagnostic suppressed, instead of being rejected outright — the old design's forced reject-and-retry cycle was incidentally what kept that specific error visible. Unconfirmed whether hydra's "file not found" is stdout or stderr; worth checking the next run's transcript for hydra calls combined with `2>/dev/null` producing silent/uninformative failures. |
| **Dry-run mode** | Commands are printed but not executed. Permanent mode flag, not a temporary debug feature. |

### Execution

| Mitigation | Detail |
|---|---|
| **Per-command timeout** | The command is wrapped as `timeout <N> /bin/bash -c <cmd>` inside the container, so the process is killed at the container level after the configured limit (default: 60s). Exit code 124 signals a timeout. A Python-level grace period (`timeout + 10s`) catches the edge case where `docker exec` itself hangs. |
| **Container memory limit** | Not yet configured. Intended mitigation — to be added to the Compose file before RL training runs. |
| **Container CPU limit** | Not yet configured. Intended mitigation — to be added to the Compose file before RL training runs. |
| Already in place: **no external network egress** | `internal: true` on the Docker network. Commands attempting to reach external hosts fail at the network layer. |
| Already in place: **capability restrictions** | `cap_drop: ALL`, `cap_add: NET_RAW` on the attacker container. No privilege escalation possible. |

### Output Handling

| Mitigation | Detail |
|---|---|
| **Minimal verbosity instruction** | The LLM is instructed via the system prompt to avoid verbose flags. Primary mechanism for keeping output manageable. See ADR 004. |
| **Hard truncation (start + end)** | Fallback if output exceeds the configured character limit. First and last halves of the limit are preserved; the middle is dropped. Truncation is indicated explicitly in the tool result. See ADR 004. |
| **ANSI escape code stripping** | Terminal colour codes are stripped before output is returned to the LLM. |
| **Exit code reporting** | Exit code is always included in the tool result. Gives the LLM a clean success/failure signal without relying on output parsing. |

### Prompt Injection

| Mitigation | Detail |
|---|---|
| **Tool result framing** | Command output is always returned as a structured tool result, never injected raw into the system or user turn. The OpenAI tool calling format enforces this. |
| **Labelled external data** | Service banners, HTTP responses, and any data originating from the target are wrapped and labelled as tool output, not treated as instructions. |

### Session Controls

| Mitigation | Detail |
|---|---|
| **Maximum steps per episode** | Hard step limit enforced by the agent loop. Configurable per experiment in the YAML config. |
| **Command audit log** | Every command is logged with a timestamp before execution. Exit code and truncated output are logged after. Full trace of agent behaviour for research analysis. |

## Known Limitations

- **`python3` bypassing the *binary allowlist* specifically (shelling out to reach an unlisted binary) is now blocked** (2026-07-31, see blocklist table above) — found during the scenario-006 (Redis) case study, where `python3 -c "import os; os.system('ssh-keygen ...')"` reached `ssh-keygen` despite it not being in `ALLOWED_BINARIES` at the time. `ssh-keygen` was subsequently added directly to the allowlist since it's a real, required tool (SSH-key-based Redis RCE), and the general `os.system`/`subprocess`/`os.popen` shell-out pattern is now blocked so this doesn't silently recur for the next tool that isn't yet allowlisted.
- **`python3` still bypasses the *dangerous pattern* blocklist for direct file-API calls**: `os.remove`, `shutil.rmtree`, etc. don't shell out (no `os.system`/`subprocess`/`os.popen` call), so they aren't covered by the `rm`/`dd`/`mkfs` text patterns either (`shutil.rmtree` doesn't even contain `rm` as a whole word — `\brm\b` doesn't match inside `rmtree`). Still accepted as-is: the container sandbox (`cap_drop`, resource limits) is the actual safeguard against container damage from these paths, and this is a narrower, lower-value gap than the binary-allowlist bypass (destroying the attacker container's own disposable filesystem doesn't threaten measurement integrity the way silently reaching an uncurated tool did).
- **Pipe chains to shell**: A command like `nmap ... | bash` passes all checks. The pipe sends nmap output to bash inside the container; in practice nmap output is not valid bash, but the structural bypass exists.

The pipe-chain and direct-file-API gaps remain accepted. The executor blocks obvious shell-level destructive commands and (now) shell-out-based allowlist bypasses; the container isolation layer handles the rest.

## Testing

`tests/test_executor.py` covers the validation layer: allowlist rejections, blocklist pattern matches, dry-run, truncation, ANSI stripping, timeout annotation (exit code 124), and that `subprocess.run` is called with a list (not `shell=True`). All tests are fast and offline — `subprocess.run` is mocked. The `docker exec` call itself is not tested in isolation; correct end-to-end behaviour is verified during sandbox integration runs.

## Files

- `agent/executor.py` — executor implementation
- `tests/test_executor.py` — unit tests for the validation layer
- `sandbox/compose/scenario-001.yml` — resource limits (memory, CPU)