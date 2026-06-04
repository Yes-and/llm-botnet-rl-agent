# ADR 001 — Testing Approach

## Decision

Minimal test suite targeting only the RL-critical path. No broad coverage goal.

Tests cover:
- **Reward function** (`tests/test_reward.py`) — silent bugs here corrupt training without obvious symptoms.
- **Action parser** (`tests/test_action_parser.py`) — LLM output is unpredictable; parsing edge cases need explicit verification.
- **Environment interface** (`tests/test_environment.py`) — the `reset()`/`step()` contract is now defined (see ADR 006); implementation pending.

All tests are fast and offline. LLM API and Docker interactions are mocked.

## Rationale

This is a research project. Time spent on broad coverage is time not spent on experiments. The reward function and action parser are the two places where bugs cause silent, hard-to-detect failures — the agent trains, produces numbers, but learns the wrong thing. Everything else fails loudly or is caught during manual experiment runs.