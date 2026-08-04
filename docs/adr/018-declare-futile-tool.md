# ADR 018: Opt-in `declare_futile` Tool, Gated Off By Default

**Status:** Accepted

## Context

The project's main RL goal is guiding the LLM better than it can guide itself. One concrete question under that: can the LLM recognize, on its own, when a target is unproductive (hardened, no valid credentials reachable) and stop instead of burning the rest of its step budget? Testing this requires giving the LLM a way to signal "I give up" — a `declare_futile` tool call that ends the episode.

An earlier, similar tool (also named `declare_futile`, plus a `declare_success` counterpart) was built and tested on the `early_stopping` branch (June/July 2026), against hardened/soft pairs of scenario-003's SSH/Redis/Mongo hosts, using MiniMax-M2.7 and Kimi-K2.5/K3. Finding: **`declare_futile` was never called, in any run, against any target** — including a case with complete evidence and explicit self-aware reasoning about exhausted attempts. That branch was discontinued (not merged) because the broader triage-predictor experiment it was part of wasn't yielding results.

**This ADR's tool is a fresh implementation, not a port.** Per explicit user direction (2026-08-04): the `early_stopping` finding is to be treated as indicative only, not conclusive — it used smaller/older models than this project's current default (GLM-5.2), so it's being retested rather than skipped or assumed to still hold.

## Decision

Add `declare_futile` as a second tool alongside `execute_command`, but gate it behind `EpisodeConfig.declare_futile: bool = False` (config field `declare_futile: true` in YAML). When `False` — the default, and every config that predates this change — the tool is absent from the API request's tool list and the system prompt is unmodified; behavior and token cost are byte-for-byte identical to before this change. Only a config that explicitly opts in sees the tool at all.

Two new case-study configs opt in: `s004-case-telnet-glm-52-declare-futile.yml` (vulnerable target) and `s004-case-telnet-hardened-glm-52.yml` (new hardened target, `sandbox/compose/scenario-004-hardened.yml`) — same task prompt as the vulnerable scenario, only the target's credential differs, isolating whether the LLM correctly distinguishes them.

## Why gated, not always-on

1. **Reproducibility** (CLAUDE.md: "if a run isn't configured, it didn't happen"). S004/S005/S006 already have citable, batch-confirmed results (e.g. S006's 10/10 GLM-5.2 batch). Adding a new tool to the shared `SYSTEM_PROMPT`/`TOOLS` constants unconditionally would silently change what those configs do on any rerun, breaking the "same config, same conditions" guarantee those results depend on.
2. **Reversibility, asked for explicitly before implementation.** A boolean defaulting to off means disabling this experiment is a one-line flip in a config, and removing the feature entirely is a small, isolated diff (the flag, `DECLARE_FUTILE_TOOL`, and the one branch in `agent/llm_client.py`/`agent/loop.py`) — nothing else in the codebase depends on it existing.

## Implementation notes

- `agent/tools.py`: `DECLARE_FUTILE_TOOL` (separate dict, not folded into `TOOLS`) and `_DECLARE_FUTILE_HINT` (appended to `SYSTEM_PROMPT` only when enabled).
- `agent/llm_client.py`: `LLMClient.__init__(declare_futile=False)` builds `self._tools` once; `complete()` dispatches on `tool_call.function.name` before the existing `execute_command` arg-parsing path.
- `agent/loop.py`: `CommandRequest.tool_name` (default `"execute_command"`) lets `run_episode()` branch without executing a shell command — reuses the existing `EpisodeResult.stop_reason` field (already used for the no-tool-call `ValueError` case) rather than adding a new field, so `scripts/run_case_study.py`'s existing early-stop printing and the batch runner's CSV need no changes.
- Purely observational, per explicit user direction: no pass/fail bar beyond "did it call the tool, and on which target" — no reward, no grading logic attached.

## Alternatives considered

**Resume directly on the `early_stopping` branch**, which already has working tooling and hardened/soft host pairs. Rejected — that branch's model/scenario choices are stale (scenario-003 hosts, pre-GLM-5.2), and the user wants this run on the now-validated S004 scenario with the current default model, not a resurrection of the old branch.

**Always-on (no gate)**, matching how `execute_command` itself has no toggle. Rejected for the reproducibility reason above — `execute_command` isn't a variable being tested; `declare_futile` explicitly is.

## Outcome (2026-08-04)

Retested independently of the `early_stopping` branch's finding, and landed in the same place via a different mechanism: across 2 hardened-target runs, `declare_futile` fired 0 times — but not because the model judged the target hardened and pushed through regardless. Each run was derailed by a different self-authored exploit-verification bug before it ever reached a stable, error-free view of its own repeated failures. The vulnerable-target control run succeeded genuinely with the tool correctly silent (no false positive). Full detail: `docs/features/scenario-004-telnet-bruteforce.md`'s "Hardened variant" section and memory (`project_declare_futile_hardened_telnet`).
