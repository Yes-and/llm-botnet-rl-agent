# Logging and Episode Runner

**Status:** Implemented

## Overview

`rl/logging_setup.py` configures structured logging for all RL runs. `scripts/run_rl_episode.py` is the smoke-test entry point that runs a single episode with a random policy against a live sandbox.

## Logging Setup

`setup_logging(log_file)` installs two handlers on the root logger:

| Handler | Level | Filter | Format |
|---|---|---|---|
| Console (stdout) | INFO | `rl.*` loggers only | `%(message)s` — one clean line per step |
| File | DEBUG | all loggers | `%(asctime)s %(levelname)-8s %(name)s | %(message)s` |

The console filter suppresses noise from `openai`, `httpx`, and `httpcore` — those appear in the file log only. Call `setup_logging()` once at the entry point before constructing any `Environment`.

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
