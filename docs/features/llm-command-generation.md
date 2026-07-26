# LLM Command Generation

**Status:** Baseline verified

## Overview

The agent uses an LLM via an OpenAI-compatible chat completions API to generate shell commands in response to a task. The model receives a system prompt describing the attacker environment and available tools, and returns a structured tool call containing the command to run.

## Implementation

- `agent/llm_client.py` — `LLMClient` class and `CommandRequest` dataclass. `LLMClient.complete(messages)` calls the API and returns a `CommandRequest(command, tool_call_id, assistant_message, reasoning, error)`. `reasoning` carries the model's thinking trace when available (`reasoning_content`, or `.content` for models that put rationale there instead — see [agent-loop.md](agent-loop.md)). `error` is set (non-executable `command=""`) when the tool call itself was malformed — see "Tool Schema" below.
- `agent/tools.py` — `SYSTEM_PROMPT` (including verbosity instructions per ADR 004) and `TOOLS` schema.
- `scripts/agent_demo.py` — standalone demo script covering the full generation flow without execution. Prints the prompt, generated command, and inference time.

## Provider

`LLMClient` takes `base_url`/`api_key_env` (defaulting to DeepInfra, `https://api.deepinfra.com/v1/openai` / `DEEPINFRA_API_KEY`), since any OpenAI-compatible provider works with the same `openai` SDK call shape. Configs override these to point at a different provider — e.g. `experiments/configs/s001-case-ssh-kimi-k3-openrouter.yml` uses OpenRouter (`https://openrouter.ai/api/v1` / `OPENROUTER_API_KEY`) for `moonshotai/kimi-k3`, not available on DeepInfra.

## Model

`moonshotai/Kimi-K2.6` via DeepInfra is the default. Selected for strong tool-use reliability (see ADR 003). Other models/providers are used per-config (see `experiments/configs/`).

## Tool Schema

Single tool: `execute_command(command: str)`. The model is forced to call it via `tool_choice="required"`, but this isn't airtight in practice — two distinct failure modes exist:
- **No tool call at all** (model returns a plain-text response instead) — `LLMClient.complete()` raises `ValueError`; `agent/loop.py` catches it, records `EpisodeResult.stop_reason`, and ends the episode. `tool_choice="required"` doesn't fully prevent this for every provider/model.
- **Malformed tool call args** (wrong JSON shape, invalid JSON, non-string `command`, or genuinely garbled/corrupted content — including leaked special tokens seen in practice with Kimi-K2.5) — recoverable. `CommandRequest.error` is set instead of raising; `agent/loop.py` feeds back a short, deliberately generic error message (never the raw malformed content — it may contain leaked tokens or garbled text that could compound rather than let the model recover cleanly) and the episode continues. Full raw detail is preserved for logging only (see `scripts/run_case_study.py`), never sent to the model.

## System Prompt Guidelines

The system prompt instructs the model to:
- Use only the listed allowed binaries
- Focus actions on the target machine, not the attacker's own environment
- Issue one simple command per step — no `&&`, `||`, or pipes
- Avoid redirecting output to `/dev/null`
- Use minimum verbosity (no `-v`/`-vv` on nmap, no `-V` on hydra)
- Prefer targeted, fast commands — each command has a strict time limit; if a previous attempt timed out, use a more conservative approach

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