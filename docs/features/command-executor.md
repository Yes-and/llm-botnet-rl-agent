# Command Executor

**Status:** Implemented

## Overview

The command executor sits between the LLM and the Docker container. It receives tool calls from the LLM, validates and executes them inside the attacker container, and returns the output. It is the primary safety boundary between the LLM's generated commands and the sandbox.

## Mitigations

### Pre-execution

| Mitigation | Detail |
|---|---|
| **Binary allowlist** | Only commands starting with an allowed binary are executed. Allowed set: `nmap`, `hydra`, `netcat`, `curl`, `ssh`, `sshpass`, `python3`. Anything else is rejected; the rejection reason is returned to the LLM as the tool result so it can adjust. |
| **Dangerous pattern blocklist** | Secondary check for patterns that should never appear regardless of binary: `rm`, `dd`, `mkfs`, fork bomb syntax (`:(){ :|:& };:`), writes to `/dev/`. |
| **Dry-run mode** | Commands are printed but not executed. Permanent mode flag, not a temporary debug feature. |

### Execution

| Mitigation | Detail |
|---|---|
| **Per-command timeout** | Commands are killed after a configurable timeout (default: 60s). Prevents runaway scans, hung sessions, and infinite loops from blocking an episode. |
| **Container memory limit** | Set in the Compose file. Prevents host resource exhaustion from memory-hungry commands. |
| **Container CPU limit** | Set in the Compose file. Prevents aggressive parallel tools (e.g. hydra with many threads) from monopolising the host. |
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

## Files

- `agent/executor.py` — executor implementation
- `sandbox/compose/scenario-001.yml` — resource limits (memory, CPU)