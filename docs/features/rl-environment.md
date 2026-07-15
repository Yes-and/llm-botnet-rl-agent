# RL Environment

**Status:** Implemented (ADR 014 Phase 1 — single-host engagement, worker only; host selection is not learned yet)

## Overview

`rl/environment.py` is the Gym-like wrapper that connects the RL policy to the attacker agent. It owns the per-episode state machine: translating policy actions into LLM instructions, running commands, parsing output, and computing rewards.

The episode engages one host at a time. `reset()` populates the host pool via a scripted subnet scan; the caller then repeatedly picks a host (`start_engagement`) and drives interaction steps (`interact`) against it until the engagement ends — an exploit fires, the policy chooses `ABANDON`, or a per-engagement safety cap is hit. See `docs/adr/014-hierarchical-single-host-engagement.md` for the design rationale.

## Interface

```python
env = Environment(config)
obs = env.reset()                    # torch.Tensor [MAX_HOSTS, NUM_FEATURES]; runs the scripted initial scan
env.start_engagement(host_ip)        # host_ip must be in env._state.known_hosts()
obs, reward, done, info = env.interact(action)
# ... keep calling interact() until info["engagement_done"] ...
```

`reset()` probes the container with `echo ok` and raises `RuntimeError` immediately if it is unreachable — fail fast rather than silently running a broken episode. It then clears `EpisodeState`, `RewardCalculator`, and the LLM message history, and runs the scripted initial scan (below) before returning the observation tensor.

`interact(action)` executes one interaction step against the currently active host and returns `(obs, reward, done, info)` — `done` is the episode-level Gym signal (`step_count >= max_steps`), unchanged in meaning from before ADR 014. `info["engagement_done"]` is the new per-engagement signal; the caller should return to host selection whenever it's `True`.

## Scripted Initial Scan

`reset()` auto-runs a fixed instruction ("Discover all live hosts on the local subnet") through the normal LLM → executor → parser pipeline, *before* any policy decision and outside the step budget/reward accounting entirely. This is not a hardcoded `nmap` invocation — the environment has no notion of the sandbox's subnet CIDR, and the LLM already knows how to discover its own network. What's scripted is *that* it runs unconditionally, not *how* it runs.

Failure here (LLM error, timeout) is a hard `RuntimeError`, not a skip: an empty host pool would silently waste the entire episode, so the failure must surface immediately rather than being absorbed.

`SCAN_NETWORK` is no longer a learned action — recon-of-the-subnet is not the capability under test (ADR 014). Per-host recon (`SCAN_PORTS`, `PROBE_PORT`) remains a learned interaction action.

## Interaction Step Flow

Each `interact(action)` call (except `ABANDON`, below) runs:

1. **Translate** — `_action_to_instruction(action, ip)` produces a one-sentence natural-language task for the LLM, `ip` being the active host.
2. **LLM call** — append the instruction as a user message; call `LLMClient.complete()` to get a shell command (and, if the model emits one, its reasoning trace).
3. **Execute** — run the command via `Executor` inside the attacker container.
4. **Parse** — `parse_step(command, output, exit_code)` returns state feature updates and an optional `ExploitEvent`.
5. **Update state** — apply all feature updates to `EpisodeState`; mark `(ip, action)` as tried.
6. **Deduplicate and attribute** — an `ExploitEvent` is only forwarded to `RewardCalculator` if (a) this is the first detection of `shell_access` on that host, **and** (b) `exploit.host` matches the currently active host. `exploit.host` comes from a regex over the LLM's own command string, not from which host the engagement targets — nothing about executing the command constrains which host it names. Without check (b), an exploit against a host the engagement didn't target would still be credited to the action the policy sampled, reinforcing the wrong pair for a result it didn't cause. State updates from the command always apply regardless of (b) — the exploit genuinely happened in the simulated world, it's only the RL credit that's withheld.
7. **Reward** — `RewardCalculator.step(exploit)` applies `+10` (exploit) and `-0.1` (step penalty).
8. **Pool removal** — if the exploit was credited, the host is removed from the pool (`EpisodeState.remove`) rather than renamed or masked — it can't be re-engaged, which is now the dedup mechanism (replaces ADR 012's `shell_access` host-slot mask).
9. **Engagement/episode signals** — `engagement_done` is set if the exploit was credited or the per-engagement safety cap (`max_engagement_steps`) was hit; `done` is set if the global step budget is exhausted.

## `ABANDON`

`ABANDON` is a learned action, available in every interaction step, that ends the current engagement early. It is **mechanical, not LLM-driven** — no instruction is sent, no command is issued — since it's a control-flow decision about episode structure, not a shell task. It still costs the standard `-0.1` step penalty and consumes one unit of the global step budget.

## Safety Cap

`EnvironmentConfig.max_engagement_steps` (default 10) is a ceiling, not a target length — an engagement that hits it without an exploit ends there, control returns to host selection. Skipped tries (LLM error, timeout) count against the cap too, so a run of pure API failures can't keep one engagement alive forever; unlike the episode-level `done` signal, a skip does *not* end the engagement on its own.

## Config

```python
@dataclass
class EnvironmentConfig:
    container_name: str       # Docker container to exec into
    max_steps: int = 40
    max_engagement_steps: int = 10  # safety cap per engagement (ADR 014)
    dry_run: bool = False
    timeout: int = 60
    max_output_chars: int = 4000
    model: str = "moonshotai/Kimi-K2.6"
    context_window: int = DEFAULT_CONTEXT_WINDOW  # = 3; number of recent step exchanges retained in LLM history
    api_timeout: int = 60     # seconds before an LLM API call is aborted and retried
    reasoning_effort: str | None = None  # DeepInfra reasoning_effort field; set to "none" to disable thinking on Qwen models
```

## LLM Message History

The message history gives the LLM context about recent steps. It is cleared on `reset()`, then immediately used for the scripted initial scan. Each interaction step adds:
- one `user` message (the instruction)
- one `assistant` message (the tool call)
- one `tool` message (the command output)

A sliding window limits history to the last `context_window` complete exchanges. Older exchanges are dropped after each step. This prevents the LLM from accumulating enough context to start making strategic decisions that belong to the RL policy — the LLM's role is to execute individual instructions, not to plan across many steps. The system prompt and initial task message are always retained as a fixed header. `ABANDON` doesn't touch message history at all, since it never calls the LLM.

## Diagnostics

Step log lines include `hosts=N` showing the number of known hosts at the time of the step — useful for diagnosing whether host discovery is flowing into state, and for watching the pool shrink as hosts are solved.

## Logging

Three log files are written per run (a fourth, `train.transcript.log`, is written by `scripts/train.py` — see `docs/features/logging.md`):

- `train.log` — INFO from `rl.*` and `agent.*` only. Step lines, episode summaries, warnings, errors. Small; use this for monitoring.
- `train.transcript.log` — one human-readable block per interaction step (sampled action, model thinking, issued command, output snippet, reward) — the artifact for auditing whether the LLM's command matched the RL action it was told to perform.
- `train.debug.log` — DEBUG from all loggers including the OpenAI SDK. Full request/response payloads and HTTP transport details. Large; open only when debugging a specific issue.

## Edge Cases

| Situation | Behaviour |
|---|---|
| Container unreachable at `reset()` | `RuntimeError` raised immediately with actionable message — episode does not start |
| Scripted initial scan's LLM call fails | `RuntimeError` raised immediately — an empty pool would silently waste the whole episode |
| `interact()` called with no active engagement | `RuntimeError` — caller must call `start_engagement()` first |
| `start_engagement()` on an unknown or already-solved host | `ValueError` — solved hosts are removed from the pool, so re-engaging one is a caller bug |
| LLM produces no tool call | Step penalty applied, step skipped (`info["skip"] = "no_tool_call"`); the dangling instruction is popped from message history to keep history well-formed. Engagement continues — same host stays active for the next interaction step. |
| Same exploit detected twice | Reward only on first detection; `shell_access` already True blocks re-reward (though in practice the host leaves the pool on first success, so a second attempt can't happen within the same episode) |
| Exploit lands on a host other than the active one | No reward — logged as `exploit on <host> ignored — engagement targeted <active host>`; state still updates for the host actually exploited; engagement is **not** ended (the targeted host wasn't compromised) |
| Unexpected exception in `interact()` | Full traceback logged via `logger.exception`; step skipped with `info["skip"] = "unexpected_error"`. Training continues rather than crashing. |
| Repeated skips within one engagement | Each skip still counts against `max_engagement_steps` — the engagement ends via the safety cap rather than running forever |

## Files

- `rl/environment.py` — implementation
- `docs/adr/005-simulation-topology.md` — observer design context
- `docs/adr/006-rl-state-action-reward.md` — state/action/reward spec
- `docs/adr/014-hierarchical-single-host-engagement.md` — single-host engagement design (current); superseded the per-step host re-selection semantics of ADR 007/010, the multi-try block mechanism of ADR 011, and the `shell_access` mask of ADR 012
