# Agentic Botnet Management With Reinforcement Learning

Master's thesis project. Uses reinforcement learning to drive LLM-based agents that develop and adapt offensive code — including botnet infrastructure, exploitation tools, and general pentesting capabilities — in a controlled simulation environment. Goals: benchmark LLM coding capability in adversarial contexts, probe LLM safeguard limits, and formalize a suitable RL approach for this problem class.

**Security research in a controlled, isolated environment only.** Every sandbox network is `internal: true` — no container has external internet access.

## Setup

Requires Python 3.11 and Docker (with Compose).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,analysis]"

cp .env.example .env
# fill in DEEPINFRA_API_KEY and/or OPENROUTER_API_KEY
```

## Running a scenario

Bring up a scenario's sandbox, then run against it:

```bash
docker compose -f sandbox/compose/scenario-003.yml up -d --build
bash scripts/sandbox_verify.sh   # deterministic pre-flight check, no LLM calls

# RL training (scenario-001/002/003)
python scripts/train.py experiments/configs/s003-smoke-minimax-m25.yml

# single-LLM case study (scenario-001, 004-007)
python scripts/run_case_study.py experiments/configs/s004-case-telnet-glm-52.yml

docker compose -f sandbox/compose/scenario-003.yml down
```

Swap `scenario-003.yml` for the compose file under `sandbox/compose/` matching whichever scenario you're running. Config choice determines which script and provider to use — see `docs/features/rl-training.md` and the scenario-specific docs under `docs/features/` for details.

```bash
pytest   # unit tests — no sandbox or API access required
```

## Repository layout

```
llm-botnet-rl-agent/
├── agent/              # LLM interface: API calls, prompt templates, action parsing
├── rl/                 # Custom RL: environment interface, reward functions, policy, training loop
├── sandbox/             # Docker Compose topologies + Dockerfiles for the simulated environment
│   ├── compose/         # One Compose file per scenario
│   └── images/          # Custom Dockerfiles for simulated devices and services
├── experiments/         # Experiment configs (YAML) and results
│   ├── configs/          # Versioned YAML files — one per run
│   └── results/           # Gitignored — logs, metrics, checkpoints
├── scripts/             # RL entry points (train.py, run_rl_episode.py) and infra utilities
├── docs/
│   ├── adr/             # Architecture Decision Records — design history, append-only
│   └── features/        # Feature docs and per-scenario implementation status
└── tests/
```

Start with `CLAUDE.md` for project-wide conventions, then `docs/adr/` for why the design is shaped the way it is (chronological — later ADRs may supersede earlier ones; check each doc's `Status:` line) and `docs/features/` for what a given component or scenario currently does.

## Provider configuration

LLM calls use the `openai` SDK against any OpenAI-compatible endpoint — DeepInfra by default, OpenRouter (or another provider) per-config via `base_url`/`api_key_env` fields. Never hardcoded; see any `*-openrouter.yml` config under `experiments/configs/` for the pattern.
