# Logging and Episode Runner

**Status:** Implemented

## Overview

`rl/logging_setup.py` configures structured logging for all RL runs. `scripts/run_rl_episode.py` is the smoke-test entry point that runs a single episode with a random policy against a live sandbox.

## Logging Setup

`setup_logging(log_file)` installs three handlers on the root logger plus a
dedicated, non-propagating handler for the transcript stream:

| Stream | Level | Filter | Purpose |
|---|---|---|---|
| Console (stdout) | INFO | `rl.*` only | One clean line per step |
| `train.log` | INFO | `rl.*` + `agent.*` | Step lines, episode summaries, warnings, errors |
| `train.transcript.log` | INFO | `rl.transcript` only (no propagate) | One human-readable block per interaction step: sampled action, model thinking, issued command, output snippet, reward |
| `train.debug.log` | DEBUG | all loggers | Full audit trail incl. raw LLM payloads |

The console/`train.log` filters suppress noise from `openai`, `httpx`, and `httpcore` — those appear in the debug log only. Call `setup_logging()` once at the entry point before constructing any `Environment`.

### Transcript stream

`train.transcript.log` exists to answer one question: **did the LLM's command match the RL action it was told to perform?** The RL action is only a label on a natural-language instruction; the LLM can ignore it and run something else, which would corrupt reward attribution. Each interaction step logs the sampled action next to the model's reasoning and the actual command, so mismatch can be audited by eye after a run. Emitted by `Environment._log_transcript`; the model's thinking trace is captured in `LLMClient.complete` (`reasoning_content`/`reasoning`, empty for non-reasoning models — their rationale lands in `content` instead). Episode boundaries are marked by `scripts/train.py`, which owns the episode counter.

## Random Episode Runner

`scripts/run_rl_episode.py` runs one episode and prints a summary. Used to smoke-test the environment before the policy network exists.

```bash
python scripts/run_rl_episode.py experiments/configs/s002-rl-001.yml
python scripts/run_rl_episode.py experiments/configs/s002-rl-001.yml --log-file custom.log
```

### Random Policy

Samples uniformly from all currently valid `(action, host_idx)` pairs each step. `DO_NOTHING` is excluded to avoid wasting steps before any hosts are discovered. Valid pairs are:

- All broadcast actions except `DO_NOTHING` (available from step 1)
- All per-host actions where `is_valid(action, host_features)` returns True (available once hosts are discovered)

### Config Format

```yaml
container_name: s002_attacker   # Docker container to exec into
max_steps: 40
model: moonshotai/Kimi-K2.6
timeout: 60
max_output_chars: 4000
seed: 42                        # Seeds random.seed() for the random policy
```

The `seed` field is read by the script; it is not part of `EnvironmentConfig`.

## Files

- `rl/logging_setup.py` — logging configuration
- `scripts/run_rl_episode.py` — random policy episode runner
- `experiments/configs/s002-rl-001.yml` — example config for scenario-002
