# ADR 008: Experiment Tracking Approach

**Status:** Accepted

## Context

The training loop is functional and the next phase is running multiple training experiments with varying hyperparameters, reward functions, scenarios, and RL algorithms. We need a way to record and compare runs.

Key constraints:
- The RL setup is not yet stable — algorithm (REINFORCE vs. PPO), reward shaping, action space, and scenario difficulty are all subject to change during the research phase.
- Runs from different phases (different reward functions, different state representations) are not directly comparable even if the hyperparameters are the same. The git commit at run time is the only reliable anchor for interpreting a historical result.
- The primary output is a thesis, so run data needs to be reproducible and plottable, not just browsable in a UI.

## Decision

**Phase 1 (now): structured local logging**

Each run writes two files to `experiments/results/<run_id>/`:

- `run_metadata.json` — git commit hash, timestamp, full config snapshot (all YAML values). The commit hash is the critical field: it pins the exact reward function, state representation, and environment code, making old runs interpretable after code changes.
- `rewards.csv` — one row per episode with the following columns:
  - `episode, total_reward, loss, exploit_count, elapsed_s` — core learning curve metrics
  - `entropy` — mean policy entropy across steps in the episode. The primary diagnostic for REINFORCE collapse: if entropy drops to near-zero, the policy has committed to a single action and stopped exploring.
  - One count column per action (`act_do_nothing`, `act_scan_network`, `act_scan_ports`, `act_probe_port`, `act_brute_force_ssh`, `act_brute_force_ftp`, `act_brute_force_telnet`, `act_connect_ssh`, `act_connect_ftp`, `act_connect_telnet`, `act_probe_http`, `act_probe_redis`, `act_probe_mongo`) — how many times each action was selected during the episode. Reveals whether the policy is stuck in degenerate behaviour (e.g. always DO_NOTHING) without requiring per-step logs.

A small `scripts/analyze.py` reads one or more results directories and plots reward/loss curves. Output goes to `experiments/results/<run_id>/` alongside the data.

**Phase 2 (deferred): MLflow**

MLflow is deferred until the RL setup stabilises — specifically, until the algorithm, reward function, and scenario structure are fixed enough that cross-run comparison is meaningful. At that point:

- MLflow's run-comparison UI becomes genuinely useful for hyperparameter sweeps.
- Migration is straightforward: `rewards.csv` imports cleanly into MLflow as logged metrics.
- The server can be run locally (`mlflow ui`) with no hosted infrastructure needed.

## Alternatives Considered

**TensorBoard (standalone):** Familiar (user has TensorFlow background), good for live scalar plots during a run. Rejected as the primary store because it doesn't capture config/commit metadata alongside metrics, making run provenance harder.

**MLflow immediately:** Better UI for comparison. Rejected for Phase 1 because MLflow's value is highest within a fixed algorithm — during exploration, old runs under a different reward function pollute the comparison view more than they help.

**Wandb:** Strong tooling but adds an external dependency and sends data to a third-party service. Out of scope for a controlled research environment.

## Consequences

- `scripts/train.py` gains two file writes at start/end of training: `run_metadata.json` (on startup, after seeds are set) and `rewards.csv` (updated after each episode).
- `scripts/analyze.py` is a new script; minimal scope (load CSVs, produce plots, exit).
- When the RL setup stabilises, add `mlflow` to `pyproject.toml` `[analysis]` dependency group and instrument `train.py` with `mlflow.log_metric` calls. The local files remain as a fallback.
