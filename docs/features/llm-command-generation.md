# LLM Command Generation

**Status:** Baseline verified

## Overview

The agent uses an LLM via the DeepInfra API (OpenAI-compatible) to generate shell commands in response to a task. The model receives a system prompt describing the attacker environment and available tools, and returns a structured tool call containing the command to run.

## Implementation

- `agent/llm_client.py` — `LLMClient` class and `CommandRequest` dataclass. `LLMClient.complete(messages)` calls the API and returns a `CommandRequest(command, tool_call_id, assistant_message)`.
- `agent/tools.py` — `SYSTEM_PROMPT` (including verbosity instructions per ADR 004) and `TOOLS` schema.
- `scripts/agent_demo.py` — standalone demo script covering the full generation flow without execution. Prints the prompt, generated command, and inference time.

## Model

`moonshotai/Kimi-K2.6` via DeepInfra. Selected for strong tool-use reliability (see ADR 003).

## Tool Schema

Single tool: `execute_command(command: str)`. The model is forced to call it via `tool_choice="required"`.

## Verified Results (2026-05-11)

Task: *"Scan the host 'target' for open ports and identify running services."*

| | |
|---|---|
| Generated command | `nmap -sC -sV target` |
| Inference time | 15.19s |
| Assessment | Command is correct and idiomatic — `-sC` (default scripts) + `-sV` (version detection) is standard practice for initial service enumeration |

## Known Limitations

- Inference latency (~15s) will be a bottleneck at RL training scale. See ADR 003 for mitigation options.
- Single-step only — no execution or feedback loop yet.