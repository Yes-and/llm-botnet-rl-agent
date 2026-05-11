# Agent Loop

**Status:** Implemented

## Overview

`run_episode` ties the LLM client and executor together into a multi-turn episode. The LLM generates a command, the executor validates and runs it in the attacker container, the output is fed back to the LLM as a tool result, and the cycle repeats up to a configured step limit.

## Flow

```
build_initial_messages(task)
        ↓
  LLMClient.complete(messages)  →  CommandRequest
        ↓
  append assistant_message to history
        ↓
  Executor.execute(command)  →  CommandResult
        ↓
  append tool result to history
        ↓
  record StepRecord
        ↓
  repeat up to max_steps
```

## Configuration

All parameters live in `EpisodeConfig` and map directly to experiment YAML configs:

| Field | Default | Purpose |
|---|---|---|
| `task` | — | Natural language objective given to the LLM |
| `container_name` | — | Name of the running attacker container |
| `max_steps` | 10 | Hard step limit per episode |
| `dry_run` | False | If True, executor validates but never runs commands |
| `timeout` | 60 | Per-command timeout in seconds |
| `max_output_chars` | 4000 | Output truncation limit |
| `model` | `moonshotai/Kimi-K2.6` | LLM model identifier |

## Output

`EpisodeResult` holds the task string and a list of `StepRecord` entries. Each `StepRecord` contains the step index, the `CommandRequest` from the LLM, and the `CommandResult` from the executor. This is the full trace needed for reward computation and experiment analysis.

## Notes

- The loop has no win condition — it always runs to `max_steps`. Win/loss detection belongs to the RL reward layer.
- The attacker container must be running before `run_episode` is called. Container lifecycle is a sandbox concern.

## Testing

`tests/test_loop.py` covers the loop logic offline — both `LLMClient` and `Executor` are mocked. Tests verify: correct step count, step record structure, message history growth (2 messages per step), tool call ID threading, initial message format, and config passthrough to constructors.

## Files

- `agent/loop.py` — implementation
- `scripts/run_episode.py` — runner script for scenario-001
- `tests/test_loop.py` — unit tests for the loop logic
