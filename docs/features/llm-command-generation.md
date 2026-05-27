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

Single tool: `execute_command(command: str)`. The model is forced to call it via `tool_choice="required"`. Some models ignore this when they consider the task complete and return a plain-text summary instead — the loop handles this gracefully by catching the resulting `ValueError` and ending the episode.

## System Prompt Guidelines

The system prompt instructs the model to:
- Use only the listed allowed binaries
- Focus actions on the target machine, not the attacker's own environment
- Issue one simple command per step — no `&&`, `||`, or pipes
- Avoid redirecting output to `/dev/null`
- Use minimum verbosity (no `-v`/`-vv` on nmap, no `-V` on hydra)

## Verified Results (2026-05-11)

Task: *"Scan the host 'target' for open ports and identify running services."*

| | |
|---|---|
| Generated command | `nmap -sC -sV target` |
| Inference time | 15.19s |
| Assessment | Command is correct and idiomatic — `-sC` (default scripts) + `-sV` (version detection) is standard practice for initial service enumeration |

## Known Limitations

- Inference latency (~15s) will be a bottleneck at RL training scale. See ADR 003 for mitigation options.

## Full Multi-Turn Loop

`agent/loop.py` implements the full episode loop — LLM generates a command, executor runs it, output is fed back as a tool result, repeat. See [agent-loop.md](agent-loop.md).