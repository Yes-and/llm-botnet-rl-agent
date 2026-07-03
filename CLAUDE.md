# llm-botnet-rl-agent

Master's thesis project. Uses reinforcement learning to drive LLM-based agents that develop and adapt offensive code — including botnet infrastructure, exploitation tools, and general pentesting capabilities — in a controlled simulation environment. Goals: benchmark LLM coding capability in adversarial contexts, probe LLM safeguard limits, and formalize a suitable RL approach for this problem class.

**Security research in a controlled, isolated environment only.**

---

## Architecture

```
llm-botnet-rl-agent/
├── agent/              # LLM interface: API calls, prompt templates, action parsing
├── rl/                 # Custom RL: environment interface, reward functions, policy, training loop
├── sandbox/            # Docker Compose topologies + Dockerfiles for the simulated environment
│   ├── compose/        # One Compose file per scenario
│   └── images/         # Custom Dockerfiles for simulated devices and services
├── experiments/        # Experiment configs (YAML) and results
│   ├── configs/        # Versioned YAML files — one per run
│   └── results/        # Gitignored — logs, metrics, checkpoints
├── scripts/            # All scripts: RL entry points and infra utilities
│   │                   # Naming: train.py, evaluate.py for RL; sandbox_setup.sh, sandbox_teardown.sh for infra
├── docs/
│   ├── adr/            # Architecture Decision Records
│   └── features/       # Feature docs and implementation status
└── tests/
```

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Python | 3.11 (via pyenv) | |
| LLM API | DeepInfra (OpenAI-compatible) | Use the `openai` SDK with a custom `base_url` |
| RL | Custom (PyTorch) | |
| Sandbox environment | Docker + Docker Compose | |
| Experiment config | YAML | |
| Package management | `pyproject.toml` + `pip` | Use dependency groups: `dev`, `analysis` |

## Navigating This Repo

This repository will grow large over time. Before opening any file, orient using `CLAUDE.md`, `docs/`, and directory/file names. Only read a file's implementation when the task genuinely requires it. File and directory names should be descriptive enough to understand purpose without opening them.

## Adding a Tool to the Attacker Image

When adding a new binary to the attacker container, update all five of these:

1. `sandbox/images/attacker/Dockerfile` — add the apt package
2. `agent/executor.py` — add the binary name to `ALLOWED_BINARIES`
3. `agent/tools.py` — add the binary name to the tool list in `SYSTEM_PROMPT`
4. `docs/adr/002-attacker-image.md` — update the included tools list
5. Relevant scenario feature doc — update the "Attacker Toolset" section

## Sandbox Rules

- All Docker networks must use `internal: true` — no container should have external internet access.
- Always specify an explicit `/24` subnet in the `ipam` block for every network. Without it, Docker defaults to `/16`, which lets a broad nmap scan find ghost IPs and Docker infrastructure addresses, corrupting the agent's host discovery state.
- Keep scenarios independent. Compose files under `sandbox/compose/` must not share networks or volumes.
- Avoid elevated container privileges. If `--privileged` is ever needed, document the reason explicitly.

## Experiment Conventions

- Every run must be fully reproducible: commit the config before running, set all seeds in the config (not in code), and record the model ID and environment state.
- Results are gitignored; configs are not. If a run isn't configured, it didn't happen.

## Key Design Decisions

- `agent/` and `rl/` are intentionally decoupled. LLM call logic must not depend on the RL training loop and vice versa.
- The RL environment interface follows a Gym-like `reset() / step()` contract.

## Docs Maintenance

This is a vibe-coded project. When making any non-trivial change, update the relevant docs:

- **`CLAUDE.md`** — update if architecture, stack, or project-wide conventions change.
- **`docs/adr/`** — add a new numbered ADR (e.g. `003-reward-shaping-approach.md`) whenever a non-obvious design decision is made or a significant tradeoff is accepted. ADRs are append-only; never edit a past one.
- **`docs/features/`** — update the relevant feature doc when a feature is added, changed, or removed. If no doc exists for the feature yet, create one.

Do not wait to be asked. Updating docs is part of completing a task.

## Working Style

- Never run tests, scripts, or the sandbox on the user's behalf — they prefer to run things themselves. Write the code and let them execute it.
- Whenever you ask the user to run something (a training script, smoke test, test suite, Docker command), end with a brief **"look for:"** note listing the 2–4 specific signals to report back. This prevents the user from pasting full logs — they should only share the numbers or lines that matter.

## Secrets

- API keys go in `.env` only. `.env` is gitignored.
- `.env.example` lists required variable names with placeholder values and is committed.
- Never commit `.env`, model weights, or raw experiment data.
