# llm-botnet-rl-agent

Master's thesis project. Uses reinforcement learning to drive LLM-based agents that exploit and adapt botnet code in a simulated IoT environment. Goals: benchmark LLM coding capability, probe LLM safeguard limits, and formalize a suitable RL approach for adversarial code environments.

**This is security research in a controlled, isolated environment. No code or infrastructure here is intended for use outside the lab.**

---

## Architecture

```
llm-botnet-rl-agent/
├── agent/              # LLM interface: DeepInfra API calls, prompt templates, action parsing
├── rl/                 # Custom RL: environment interface, reward functions, policy, training loop
├── lab/                # Docker Compose topologies + custom Dockerfiles for the IoT simulation
│   ├── compose/        # One Compose file per scenario/experiment
│   └── images/         # Custom Dockerfiles for simulated IoT devices and C2
├── experiments/        # Experiment configs (YAML) and results
│   ├── configs/        # Versioned YAML files — one per experiment run
│   └── results/        # Gitignored — raw logs, metrics, checkpoints
├── scripts/            # Setup, teardown, and utility scripts
└── tests/
```

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Python | 3.11 (via pyenv) | Pin in `.python-version` |
| LLM API | DeepInfra (OpenAI-compatible) | Use the `openai` SDK with a custom `base_url` |
| RL | Custom (PyTorch) | No SB3/RLlib dependency |
| Lab environment | Docker + Docker Compose | One Compose file per scenario |
| Experiment config | YAML | One file per run, committed to `experiments/configs/` |
| Package management | `pyproject.toml` + `pip` | Use dependency groups: `dev`, `analysis` |

## Dev Setup

```bash
pyenv install 3.11
pyenv local 3.11
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in DEEPINFRA_API_KEY
```

## Lab Environment Rules

- **All Docker networks must use `internal: true`.** No container in the IoT lab should have external internet access. This is non-negotiable for safety and reproducibility.
- Each scenario lives in its own Compose file under `lab/compose/`. Never share networks across scenario files.
- Tear down lab environments explicitly after each experiment run (`docker compose down -v`).
- Never run lab containers with `--privileged` unless a specific kernel-level test requires it, and document why.

## Experiment Conventions

- Every run gets a YAML config in `experiments/configs/` before it executes. The config must include: scenario name, model ID, RL hyperparameters, random seed, and date.
- Results (logs, reward curves, checkpoints) go in `experiments/results/<run-id>/`. This directory is gitignored.
- Use deterministic seeds everywhere. Set seeds in the config, not hardcoded in source.

## Key Design Decisions

- `agent/` and `rl/` are intentionally decoupled. The LLM call logic should not depend on the RL training loop and vice versa. This allows swapping RL algorithms and running ablations without restructuring.
- The RL environment interface follows a Gym-like `reset() / step()` contract, defined in `rl/environment.py`, even though `gymnasium` is not a hard dependency.
- Prompt templates live in `agent/prompts/` as plain text or Jinja2 files — not hardcoded strings in Python.

## Docs Maintenance

This is a vibe-coded project. When making any non-trivial change, update the relevant docs:

- **`CLAUDE.md`** — update if architecture, stack, or project-wide conventions change.
- **`docs/adr/`** — add a new numbered ADR (e.g. `003-reward-shaping-approach.md`) whenever a non-obvious design decision is made or a significant tradeoff is accepted. ADRs are append-only; never edit a past one.
- **`docs/features/`** — update the relevant feature doc when a feature is added, changed, or removed. If no doc exists for the feature yet, create one.

Do not wait to be asked. Updating docs is part of completing a task.

## Secrets

- API keys go in `.env` only. `.env` is gitignored.
- `.env.example` lists required variable names with placeholder values and is committed.
- Never commit `.env`, model weights, or raw experiment data.
