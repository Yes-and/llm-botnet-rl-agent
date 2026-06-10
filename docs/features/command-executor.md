# Command Executor

**Status:** Implemented

## Overview

The command executor sits between the LLM and the Docker container. It receives tool calls from the LLM, validates and executes them inside the attacker container, and returns the output. It is the primary safety boundary between the LLM's generated commands and the sandbox.

## Mitigations

### Pre-execution

| Mitigation | Detail |
|---|---|
| **Binary allowlist** | Only commands starting with an allowed binary are executed. Allowed set: `nmap`, `hydra`, `netcat`, `nc`, `curl`, `ssh`, `sshpass`, `python3`, `ping`, `ip`, `ls`, `cat`, `find`, `grep`, `echo`, `which`, `telnet`, `ftp`, `redis-cli`. Anything else is rejected; the rejection reason is returned to the LLM as the tool result so it can adjust. |
| **Dangerous pattern blocklist** | Secondary check for patterns that should never appear regardless of binary: `rm`, `dd`, `mkfs`, fork bomb syntax (`:(){ :|:& };:`), writes to `/dev/`. Note: this pattern also blocks `2>/dev/null` (a false positive). Left intentionally — suppressing stderr is not needed and the safer behaviour is to keep the block. |
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

- **`python3` bypasses the blocklist**: Python file operations (`os.remove`, `shutil.rmtree`, etc.) are not covered by the dangerous pattern list. The container sandbox (`cap_drop`, resource limits) is the actual safeguard against container damage from these paths.
- **Pipe chains to shell**: A command like `nmap ... | bash` passes all checks. The pipe sends nmap output to bash inside the container; in practice nmap output is not valid bash, but the structural bypass exists.

Both are accepted. The executor blocks obvious shell-level destructive commands; the container isolation layer handles the rest.

## Testing

`tests/test_executor.py` covers the validation layer: allowlist rejections, blocklist pattern matches, dry-run, truncation, ANSI stripping, timeout annotation (exit code 124), and that `subprocess.run` is called with a list (not `shell=True`). All tests are fast and offline — `subprocess.run` is mocked. The `docker exec` call itself is not tested in isolation; correct end-to-end behaviour is verified during sandbox integration runs.

## Files

- `agent/executor.py` — executor implementation
- `tests/test_executor.py` — unit tests for the validation layer
- `sandbox/compose/scenario-001.yml` — resource limits (memory, CPU)