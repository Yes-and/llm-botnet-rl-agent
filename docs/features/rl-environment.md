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

The scan loops across up to `_INITIAL_SCAN_MAX_TRIES` (4) exchanges rather than accepting a single shot: a reasonable agent's first move for "discover the subnet" is often checking its own network config (`ip addr`) before running the actual scan, so one exchange frequently finds zero hosts without anything being wrong. On an empty result, a follow-up instruction nudges the LLM to run the actual scan; the loop returns as soon as any host is discovered. (Found the hard way: an early smoke test's first LLM turn was `ip addr show`, which — under the original single-shot version — silently produced a 0-host episode with no error at all.)

Failure is a hard `RuntimeError`, not a skip, but only once every attempt has come back empty (or the LLM/API call itself fails) — an empty host pool would silently waste the entire episode, so it must surface loudly rather than being absorbed.

`SCAN_NETWORK` is no longer a learned action — recon-of-the-subnet is not the capability under test (ADR 014). Per-host recon (`SCAN_PORTS`, `PROBE_PORT`) remains a learned interaction action.

## Interaction Step Flow

Each `interact(action)` call (except `ABANDON`, below) runs:

1. **Translate** — `_action_to_instruction(action, ip)` produces a one-sentence natural-language task for the LLM, `ip` being the active host.
2. **LLM call** — append the instruction as a user message; call `LLMClient.complete()` to get a shell command (and, if the model emits one, its reasoning trace).
3. **Execute** — run the command via `Executor` inside the attacker container.
4. **Parse** — `parse_step(command, output, exit_code)` returns state feature updates and an optional `ExploitEvent`.
5. **Update state** — apply all feature updates to `EpisodeState`; mark `(ip, action)` as tried.
6. **Deduplicate and attribute** — an `ExploitEvent` is only forwarded to `RewardCalculator` if (a) this is the first detection of `shell_access` on that host, (b) `exploit.host` matches the currently active host, **and** (c) the exploit's vulnerability matches what the *sampled action* is actually for (`_ACTION_VULNERABILITY`: `CONNECT_SSH`→`ssh_weak_credentials`, `CONNECT_FTP`→`ftp_anonymous_login`, `CONNECT_TELNET`→`telnet_weak_credentials`, `PROBE_REDIS`→`redis_no_auth`, `PROBE_MONGO`→`mongodb_no_auth`; every other action, including all `BRUTE_FORCE_*`, maps to nothing and never earns credit directly). `exploit.host` comes from a regex over the LLM's own command string, and the LLM is free to run any command regardless of what it was asked — nothing constrains either which host it names or which tool it uses. Without (b), an exploit on the wrong host gets credited to an action that didn't cause it; without (c), an exploit via the wrong *tool* does the same (found in practice: policy sampled `BRUTE_FORCE_SSH`, LLM already had credentials from an earlier turn and just connected directly via `sshpass` — a real `ssh_weak_credentials` exploit, but that's `CONNECT_SSH`'s job, not `BRUTE_FORCE_SSH`'s). State updates from the command always apply regardless of (b)/(c) — the exploit genuinely happened in the simulated world, it's only the RL credit that's withheld. `info["compromised"]` reflects whether the active host was genuinely compromised (a)+(b), independent of (c) — see steps 8-9.
7. **Reward** — `RewardCalculator.step(exploit)` applies `+10` (exploit) and `-0.1` (step penalty). `exploit` here has already passed all three checks above.
8. **Pool removal** — if `info["compromised"]` is true, the host is removed from the pool (`EpisodeState.remove`) rather than renamed or masked — it can't be re-engaged, which is now the dedup mechanism (replaces ADR 012's `shell_access` host-slot mask). This fires even when reward was withheld for a wrong-action mismatch — the host is genuinely gone from the pool either way, since the world state is real regardless of RL credit.
9. **Engagement/episode signals** — `engagement_done` is set if `info["compromised"]` is true or the per-engagement safety cap (`max_engagement_steps`) was hit; `done` is set if the global step budget is exhausted.

## `ABANDON`

`ABANDON` is a learned action that ends the current engagement early. It is **mechanical, not LLM-driven** — no instruction is sent, no command is issued — since it's a control-flow decision about episode structure, not a shell task. It still costs the standard `-0.1` step penalty and consumes one unit of the global step budget.

**Masked out for the first `MIN_STEPS_BEFORE_ABANDON` (3) steps of an engagement** (`rl/actions.py`) — added after a smoke test showed an untrained policy sampling `ABANDON` on ~30% of decisions, simply because it's one of only 3 valid actions at engagement start. Prevents the policy from ever learning a degenerate "give up immediately, every time" optimum before it's tried anything. Gated on `Environment.engagement_step_count`, passed explicitly into `Policy.sample()`/`predict()` rather than stored as a state-tensor feature — it's masking-time context the network doesn't need as an input.

## Safety Cap

`EnvironmentConfig.max_engagement_steps` (default 10) is a ceiling, not a target length — an engagement that hits it without an exploit ends there, control returns to host selection. Skipped tries (LLM error, timeout) count against the cap too, so a run of pure API failures can't keep one engagement alive forever; unlike the episode-level `done` signal, a skip does *not* end the engagement on its own.

## State: try-counts and engagement progress

Two `rl/state.py` features give the policy richer signal than the original ADR 014 Phase 1 cut:

- **`tried_<action>` is a capped count (`MAX_TRIED_COUNT = 5`), not a flag.** `EpisodeState.mark_tried()` increments rather than sets — the policy can distinguish "tried once" from "tried repeatedly, nothing new," relevant for learning when an action (or the host) is worth abandoning. `EpisodeState.get()` still only exposes a truthy view (`bool()`) — read `to_tensor()` or `_hosts` directly for the actual count.
- **`engagement_progress`** (0..1) — how far the *active* host's current engagement is through its safety cap, set by `Environment._update_engagement_progress()` on every interaction step and zeroed by `start_engagement()`. Gives the policy a sense of urgency it didn't have before (previously nothing distinguished step 1 of 5 from step 5 of 5). Stale on a host between engagements once it's no longer active — accepted imprecision, not fixed.

`EpisodeState.set()` accepts `bool | float` now (not just `bool`) to support `engagement_progress`'s real-valued range — booleans still coerce via `float(True) == 1.0`, unchanged for every other feature.

`EpisodeState.to_tensor()` scales the `tried_*` columns to 0..1 (raw count / `MAX_TRIED_COUNT`) before returning — the policy network has no normalization layer, so an unscaled count up to 5 next to 0/1 flags in the same `Linear` input would dominate them. `mark_tried()`, `get()`, and `host_features()` all still see the raw, unscaled count; only the tensor consumed by `rl/policy.py` is normalized.

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
    api_timeout: int = 60     # seconds before an LLM API call is aborted and retried
    reasoning_effort: str | None = None  # DeepInfra reasoning_effort field; set to "none" to disable thinking on Qwen models
```

## LLM Message History

The message history gives the LLM context about the current engagement. It is cleared on `reset()`, then immediately used for the scripted initial scan. Each interaction step adds:
- one `user` message (the instruction)
- one `assistant` message (the tool call)
- one `tool` message (the command output)

**Scoped to the engagement, not the episode.** `start_engagement()` resets history back to just the system prompt + initial task header, discarding the previous engagement's conversation. Naturally bounded by `max_engagement_steps` — no separate rolling-window pruning needed (the old `context_window`/`_prune_messages` mechanism, which trimmed a rolling window across the *whole episode*, is retired as of the fix below).

This replaces an earlier design where a small rolling window (`context_window`, default 3 exchanges) slid across the entire episode regardless of which host was active. That caused a real bug: a host re-engaged later in the same episode had its already-discovered facts (e.g. an open port) correctly reflected in the RL state tensor, but the LLM's own conversation context had evicted them in favor of whatever host was engaged in between — so it re-verified information it had already found instead of acting on it directly. Resetting per-engagement instead of sliding across engagements fixes this for the *current* engagement; a host's *previous* engagement is still not carried forward on re-engagement (deliberately deferred — see `next_steps.md`).

`ABANDON` doesn't touch message history at all, since it never calls the LLM.

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
| Exploit fires via a tool/technique that doesn't match the sampled action (e.g. `BRUTE_FORCE_SSH` sampled, LLM connects directly with known creds instead) | No reward — logged as `<vulnerability> exploit ignored for reward — action=<action> doesn't earn it`; state still updates and the host **is** removed from the pool; engagement **does** end (the active host was genuinely compromised, just not by the sampled action) |
| Unexpected exception in `interact()` | Full traceback logged via `logger.exception`; step skipped with `info["skip"] = "unexpected_error"`. Training continues rather than crashing. |
| Repeated skips within one engagement | Each skip still counts against `max_engagement_steps` — the engagement ends via the safety cap rather than running forever |

## Files

- `rl/environment.py` — implementation
- `docs/adr/005-simulation-topology.md` — observer design context
- `docs/adr/006-rl-state-action-reward.md` — state/action/reward spec
- `docs/adr/014-hierarchical-single-host-engagement.md` — single-host engagement design (current); superseded the per-step host re-selection semantics of ADR 007/010, the multi-try block mechanism of ADR 011, and the `shell_access` mask of ADR 012
