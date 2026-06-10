# RL Environment

**Status:** Implemented

## Overview

`rl/environment.py` is the Gym-like wrapper that connects the RL policy to the attacker agent. It owns the per-episode state machine: translating policy actions into LLM instructions, running commands, parsing output, and computing rewards.

## Interface

```python
env = Environment(config)
obs = env.reset()                              # torch.Tensor [MAX_HOSTS, NUM_FEATURES]
obs, reward, done, info = env.step(action, host_idx)
```

`reset()` probes the container with `echo ok` and raises `RuntimeError` immediately if it is unreachable — fail fast rather than silently running a broken episode. It then clears `EpisodeState`, `RewardCalculator`, and the LLM message history, and returns an all-zero observation tensor.

`step(action, host_idx)` runs one RL step and returns `(obs, reward, done, info)`.

## Step Flow

1. **Resolve host** — map `host_idx` into `state.known_hosts()` (IP-sorted). Broadcast actions (`DO_NOTHING`, `SCAN_NETWORK`) ignore `host_idx`.
2. **Translate** — `_action_to_instruction(action, ip)` produces a one-sentence natural-language task for the LLM.
3. **LLM call** — append the instruction as a user message; call `LLMClient.complete()` to get a shell command.
4. **Execute** — run the command via `Executor` inside the attacker container.
5. **Parse** — `parse_step(command, output, exit_code)` returns state feature updates and an optional `ExploitEvent`.
6. **Update state** — apply all feature updates to `EpisodeState`; mark `(ip, action)` as tried.
7. **Deduplicate** — `ExploitEvent` is only forwarded to `RewardCalculator` the first time `shell_access` is set on that host. Subsequent detections of the same exploit return no reward.
8. **Reward** — `RewardCalculator.step(exploit)` applies `+10` (exploit) and `-0.1` (step penalty).
9. **Return** — `(obs_tensor, reward, done, info)`.

## Done Condition

An episode ends when `step_count >= config.max_steps`. Scenario-specific win conditions (e.g., all services exploited) are not yet implemented.

## Config

```python
@dataclass
class EnvironmentConfig:
    container_name: str       # Docker container to exec into
    max_steps: int = 40
    dry_run: bool = False
    timeout: int = 60
    max_output_chars: int = 4000
    model: str = "moonshotai/Kimi-K2.6"
```

## LLM Message History

The message history persists across steps within an episode, giving the LLM context about what it has already done. It is cleared on `reset()`. Each step adds:
- one `user` message (the instruction)
- one `assistant` message (the tool call)
- one `tool` message (the command output)

## Edge Cases

Step log lines include `hosts=N` showing the number of known hosts at the time the action was selected — useful for diagnosing whether host discovery is flowing into state.

## Edge Cases

| Situation | Behaviour |
|---|---|
| Container unreachable at `reset()` | `RuntimeError` raised immediately with actionable message — episode does not start |
| `host_idx` out of range for a non-broadcast action | Step penalty applied, step skipped (`info["skip"] = "invalid_host_idx"`) |
| LLM produces no tool call | Step penalty applied, step skipped (`info["skip"] = "no_tool_call"`); the dangling instruction is popped from message history to keep history well-formed |
| Same exploit detected twice | Reward only on first detection; `shell_access` already True blocks re-reward |

## Files

- `rl/environment.py` — implementation
- `docs/adr/005-simulation-topology.md` — observer design context
- `docs/adr/006-rl-state-action-reward.md` — state/action/reward spec
