# ADR 001 — Testing Approach

## Decision

Minimal test suite targeting only the RL-critical path. No broad coverage goal.

Tests cover:
- **Reward function** (`tests/test_reward.py`) — silent bugs here corrupt training without obvious symptoms.
- **Action validation** (`tests/test_actions.py`) — masking logic; ensures invalid (action, host) combinations are rejected correctly.
- **Output parser** (`tests/test_parser.py`) — table-driven, one case per real run example; covers all per-tool sub-parsers and edge cases (timeouts, rejections, partial output).
- **State** (`tests/test_state.py`) — feature matrix construction, host ordering, tensor output.
- **Environment interface** (`tests/test_environment.py`) — covers reset, state updates, reward deduplication, host resolution, skip paths, and episode termination. LLM and Docker are mocked; all tests run offline.

All tests are fast and offline. LLM API and Docker interactions are mocked.

## Rationale

This is a research project. Time spent on broad coverage is time not spent on experiments. The reward function, output parser, and environment are the places where bugs cause silent, hard-to-detect failures — the agent trains, produces numbers, but learns the wrong thing. (Example: a deduplication bug in the environment suppressed all exploit rewards without raising any error.) Everything else fails loudly or is caught during manual experiment runs.