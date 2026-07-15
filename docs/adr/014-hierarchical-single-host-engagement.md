# ADR 014: Hierarchical Single-Host Engagement — Target Selection + Options-Style Worker

**Status:** Proposed

**Would supersede (on acceptance):** the per-step host re-selection semantics in ADR 007 (Decision 1) and ADR 010; the multi-try block mechanism in ADR 011; the shell-access mask in ADR 012; and would relocate ADR 009's triage question. See "Superseded sections" below. Nothing changes until this ADR is Accepted.

## Context

The current design picks a fresh `(host, action, duration)` every environment step across a *multi-host* episode (ADR 007/010/011). Accumulated evidence — case studies plus `-001`..`-004` training runs — points at that structure, not any single bug, as the source of the persistent failures:

1. **Only single-command exploits ever fire.** Redis/MongoDB are one command (`PROBE_REDIS` → +10). SSH/FTP/Telnet need a chain (scan → ports → brute-force → connect). The reward is terminal-only, so the chain earns nothing until its last link lands, and a policy that re-picks `(host, action)` freshly each step across many hosts rarely completes that chain by chance — so the +10 that would teach it almost never appears. ADR 011's duration head narrowed this to "same action, multiple tries" but the real bottleneck is chaining *different* actions on *one* host.
2. **Reward bleeds across hosts.** REINFORCE's discounted return credits earlier actions in the episode, including actions against a *different* host, for an exploit they had nothing to do with (`gamma=0.95`, `γ^10 ≈ 0.6`).
3. **Per-step host re-selection loses the interaction thread.** Discovering a promising service on host A on one step, then re-selecting a different host on the next, throws away the momentum that should turn a discovery into an exploit.

The fix is not a new reward term (subgoal shaping was considered and rejected — see Alternatives) and not a new RL algorithm. It is to change the episode's *shape*: engage one host at a time, stay on it long enough to complete or abandon a chain, and let a target selector learn which hosts are worth engaging.

## Decision

Restructure the episode around **engaging one host at a time**, driven by a **single shared network** operating in two modes. The episode still discovers a pool of hosts; it just works them sequentially rather than interleaving them per step.

**Selection mode** — the **host head** picks the next target from the full `[MAX_HOSTS, NUM_FEATURES]` state tensor. This is the "manager" decision. It is where triage now lives: choosing not to re-engage a host that burned steps without an exploit *is* triage, learned from return.

**Interaction mode** — the **action head**, conditioned on the active host's feature vector (reusing ADR 010's mechanism), drives commands against that one host: recon → brute-force → connect, re-deciding the action each step. State updates land in the active host's feature vector and are visible to the *next* interaction step, so the worker capitalizes on its own discoveries in place.

**Options-style termination.** An engagement runs until one of:
- **Success** — an `ExploitEvent` fires; the host is **removed from the target pool** (not renamed — see Alternatives).
- **Abandon** — a learned `ABANDON` action, available only in interaction mode, ends the engagement.
- **Safety cap** — a maximum number of interaction steps per engagement, so a stuck worker can't run forever. This is a *ceiling*, not a target length.

On termination, control returns to selection mode. The episode ends at the global `max_steps` budget or when the pool is exhausted.

**Reward stays terminal-only.** Exploit `+10`; step penalty `−0.1` on each interaction step (including `ABANDON`); selection steps are free (reward 0). No subgoal rewards. **Economy is emergent**: a worker that keeps probing a worthless host bleeds `−0.1`/step with no `+10`, so the return-maximizing behavior is to `ABANDON` early — the agent learns *when to stop* from the penalty it already has, without being told what a subgoal is worth. Severity- or resource-weighted terminal reward remains open (the user is amenable) but is a separate future ADR, still terminal-only.

## Two modes, one network

```
state : [MAX_HOSTS, NUM_FEATURES]          # full pool, as today
h = f_trunk(state.flatten())               # shared trunk, trained by every step of both modes

# selection step (no active host):
host_slot ~ Categorical(softmax(W_host · h))     # → sets active host, enters interaction mode

# interaction step (active host = a):
action_input = [h ∥ x_a]                          # x_a = active host features (ADR 010)
action ~ Categorical(softmax(W_action · action_input))   # includes ABANDON
# execute one primitive command against a; update state; stay in interaction mode
# unless action==ABANDON, exploit fired, or safety cap hit → return to selection
```

Sharing the trunk is deliberate and is an advantage over two separate networks: the trunk is shaped by *every* interaction step, so the representation the host head reads for selection is informed by all the worker's experience — a standalone manager would have to learn its host representation from only the sparse selection-step gradients.

## Why REINFORCE remains valid

Each decision — selection or interaction — is one entry in the trajectory with its own `log_prob` (`log π_host` or `log π_action`). Training is flat REINFORCE over the interleaved sequence, with **one change to credit assignment: returns are scoped to the engagement.** `_compute_returns` resets the running discounted return to zero at each selection boundary, so an exploit on host A backpropagates only within A's engagement segment — never across the selection boundary to a previous host B. This directly removes the cross-host bleed (Context #2) that a single episode-wide return would still allow. The selection decision sits at the head of its segment, so its `G_t` is the discounted return of the engagement it launched — exactly the "was this host worth engaging" signal the selector needs. This is standard semi-MDP/options treatment; no gradient passes through the discrete samples.

## What changes vs. current code

- **`rl/state.py`** — the `[MAX_HOSTS, NUM_FEATURES]` tensor is retained (the selector needs the whole pool). The environment additionally tracks the current active host and mode.
- **`rl/policy.py`** — `sample()` takes the mode and active host. The host head is reused for selection; the conditioned action head (ADR 010) is reused for interaction. `ABANDON` is added to the action space. **The duration head (ADR 011) is retired** — worker persistence on one host subsumes it (re-selecting the same action on consecutive steps *is* multi-try; the safety cap gives room for retries).
- **`rl/environment.py`** — a per-engagement interaction loop replaces `step_block`'s same-action loop: run commands against the active host until exploit, `ABANDON`, or safety cap, then hand back to selection. Host removed from the pool on success (reuses ADR 012's `shell_access` dedup intent, now expressed as pool removal). The wrong-host reward gate from ADR 012 is **retained** — the LLM can still name a different host in a command string even with one active host.
- **`rl/environment.py` reset** — the **initial subnet scan is scripted** (auto-run on `reset()` to populate the pool) rather than a learned action. Recon-of-the-subnet is not the capability under test; finding and exploiting the per-host vulnerability is. Within-host service discovery (`SCAN_PORTS`/`PROBE_PORT`) stays a learned interaction action. `# ponytail:` this is a deliberate simplification to avoid modeling a broadcast action inside selection mode — revisit if network-discovery behavior becomes a research target.
- **`scripts/train.py`** — `_compute_returns` gains the per-engagement reset described above; the trajectory now interleaves selection and interaction entries.
- **`rl/reward.py`** — unchanged. Terminal-only, as today.

## Staging — build the worker before the selector

The selector is the part most likely to fail: under REINFORCE it gets only a handful of gradient signals per episode (one per engagement) and is data-starved. Do not build both halves at once.

- **Phase 1 — worker only.** One host per episode, target chosen randomly/round-robin (selector stubbed, host head not trained). Tests the core bet cheaply, with ordinary per-step REINFORCE and no two-mode credit assignment: *does a focused worker discover and complete SSH/FTP/Telnet chains, and learn to `ABANDON` unproductive hosts?* This is close to the already-scoped single-host redesign — mostly removing per-step host re-selection.
- **Phase 2 — learned selector.** Enable the host head and engagement-scoped returns only once Phase 1 shows workers completing chains. If Phase 1 workers *cannot* learn chains even focused on one host, the blocker was never the architecture — it is terminal reward over a 4+ step chain being too sparse for REINFORCE to bootstrap from zero, and the response is to revisit severity/resource-weighted terminal reward or a value-based method, not to add the selector.

## Alternatives considered

**Two separate networks (manager + worker).** The clean hierarchical form. Deferred, not rejected: it doubles sample complexity and starves the manager, and the shared trunk already gives the selector worker-informed representations for free. Promote to two networks only if the shared one demonstrably underperforms.

**Fixed selection cadence (re-select every N steps).** Rejected. A fixed budget is wrong in both directions: it over-runs easy hosts (wasted `−0.1` steps) and, worse, *cuts long chains off mid-sequence* — an SSH chain that needs 6 steps but is capped at 5 never fires its exploit, reintroducing the exact sparse-chain failure this ADR exists to fix. Termination must be a condition (success / `ABANDON` / safety cap), not a clock.

**Keeping the duration head.** Redundant here: single-host persistence already provides multi-step commitment at a finer grain than a per-decision try-budget, and re-selecting the same action covers within-action retries.

**Subgoal / potential-based reward shaping.** Rejected on the user's standing philosophy: only successful exploits represent real progress; the agent should discover the intermediate steps (or novel untested ones) on its own. The step penalty already supplies the economy pressure that shaping would otherwise provide.

**Renaming a solved host as "new."** Rejected — it defeats the `(host, vulnerability)` dedup and the compromised-host mask, letting the worker farm the same `+10` indefinitely. Solved hosts leave the pool; the pool is replenished by the next episode's reset, not by resurrecting solved targets. Note this is distinct from **replacing a solved host with a genuinely new random host** (distinct IP, fresh state, its own dedup key) — that carries no farming risk and is a legitimate mechanism, but see Non-goals: it is a deferred Phase-2 lever, not part of the base design.

## Non-goals

- **Multi-target simultaneous attack** — out of scope by design; one active host at a time.
- **Cross-host loot / lateral movement** (credential or wordlist reuse across hosts) — explicitly deferred. Current scenarios have independent, individually-vulnerable targets, so per-host loot suffices. Modeling loot as shareable state is only worth it for a scenario deliberately built so host A is exploitable *only* with loot from host B — a future research choice, not a prerequisite here.
- **Mid-episode fresh-host replacement** — deferred, not in the base design. In Phase 1 the episode simply ends on pool exhaustion or `max_steps`; the reset replenishes with a fresh random pool. Replacement's benefit — keeping engagements-per-episode high — accrues to the *data-starved learned selector*, so it is a **Phase-2 lever**, added only if the selector is observed starving. When added, script-inject the replacement (distinct IP) straight into the state tensor rather than forcing an LLM re-scan, unless learning to re-discover targets under non-stationarity is itself made a research goal.

## Open questions

- Exact `ABANDON` cost and whether selection steps should consume the global `max_steps` budget (leaning: selection free, interaction including `ABANDON` costs `−0.1` and one budget unit).
- Severity- or resource-weighted terminal reward (user is amenable) — its own ADR once specced.
- Duration-head retirement — confirm against Phase 1 results before deleting the code path.
- Scripted vs. learned initial subnet scan — revisit if host-discovery becomes a research target. Coupled to replacement: a forced/learned scan only earns its cost if mid-episode replacement exists *and* re-discovery is something to benchmark; otherwise scripted population (initial and any replacement) is strictly cheaper.

## Superseded sections (on acceptance)

**ADR 007:**

| Section | What would change |
|---|---|
| Decision 1, `∇J = Σ_t G_t · ∇ log π(a_t, h_t \| s_t)` | `t` indexes a selection or interaction decision, not a joint per-step `(host, action)` pick. Returns are engagement-scoped (reset at selection boundaries), not episode-wide. |

**ADR 010:**

| Section | What would change |
|---|---|
| Joint `π(host, action \| state)` sampled every step | Host and action are no longer sampled jointly per step. The host is chosen in selection mode and held fixed through an engagement; the conditioned action head is reused unchanged for interaction steps. |

**ADR 011:**

| Section | What would change |
|---|---|
| Action-duration head and `step_block` multi-try mechanism | Retired. Single-host persistence subsumes per-decision try-budgets; the engagement loop replaces `step_block`. |

**ADR 012:**

| Section | What would change |
|---|---|
| `shell_access` host-slot mask | Expressed instead as removing the solved host from the pool. The wrong-host exploit-attribution gate is **retained**. |

**ADR 009:**

| Section | What would change |
|---|---|
| Triage as within-episode host-prioritization | Relocated to the selector (host head): triage is choosing not to re-engage unproductive hosts, learned from engagement-scoped return. |

## Phase 1 implementation notes

Appended during initial Phase 1 implementation on a feature branch (status still Proposed — these are choices made while building, not a decision to Accept). Resolves several of the "Open questions" above for Phase 1 specifically; Phase 2 may revisit.

- **`DO_NOTHING` retired**, not just `SCAN_NETWORK`. With the scripted scan always populating the pool before any policy decision, engagement mode always has an active host — `DO_NOTHING`'s original purpose (filler before any host existed) no longer applies, and `ABANDON` already covers "not worth pursuing."
- **`ABANDON` is mechanical, not LLM-driven** — no instruction sent, no command issued. It's a control-flow decision about episode structure, not a shell task. Still costs `-0.1` and one step-budget unit (resolves the "exact ABANDON cost" open question for Phase 1: selection is free, `ABANDON` costs like any interaction step).
- **Action masking reuses `rl/actions.py`'s `is_valid()`** (previously only used by the random policy in `scripts/run_rl_episode.py`) rather than the narrower creds_found-only mask the pre-ADR-014 policy had. This gives the "equally likely with nothing known, narrows as the engagement learns" behavior for free — right after discovery, `is_valid()` only passes recon actions and `ABANDON`; each subsequent discovery unmasks the actions it enables.
- **`PROBE_HTTP` has no matching parser branch** (`rl/parser.py` never emits a state update or exploit for it) — a pre-existing gap, not introduced by this refactor. Left in the Phase 1 action space rather than removed: `is_valid()` masks it out on any host that never reports port 80/443 open, and no current scenario target has an HTTP exploit path, so the gap is masked into irrelevance rather than fixed. Revisit if a future scenario adds an HTTP-vulnerable target.
- **Phase 1 host selection is uniform random** each time an engagement ends (not round-robin) — simplest placeholder, no state to track, since it's replaced wholesale by the learned host head in Phase 2.
- **Safety cap default: `max_engagement_steps = 10`**, exposed as an `EnvironmentConfig`/YAML field — a starting guess, easy to retune from data rather than logic.
- **Transcript logging** (`train.transcript.log`, see `docs/features/logging.md`) was added alongside this refactor to measure a related risk before deciding whether to address it: the RL action is only a label on a natural-language instruction, and nothing enforces that the LLM's actual command matches it. Rather than building an enforcement gate speculatively, Phase 1 ships with a human-readable per-step transcript (sampled action next to the model's reasoning and actual command) so action/command mismatch can be measured from real runs first.
- **Episode structure deviates from this section's literal text, by deliberate choice.** This section says "one host per episode... ordinary per-step REINFORCE and no two-mode credit assignment," implying episode-scoped and engagement-scoped returns would be identical (one engagement per episode). What's actually implemented: `scripts/train.py` runs *many* engagements per episode (sequential hosts, one after another) until the global `max_steps` budget is exhausted or the pool empties, with one gradient update at episode end covering all of them via the engagement-scoped return-reset in "Why REINFORCE remains valid" above. Chosen over the literal one-host-per-episode reading because it uses the full step budget's worth of real data per gradient update instead of mostly discarding it (a single engagement often ends in a handful of steps), which should mean lower variance per update — directly relevant given this project's already-documented REINFORCE variance problems (see `next_steps.md`'s deferred backlog). Confirmed explicitly with the user 2026-07-15 after the discrepancy surfaced mid-conversation, rather than being decided silently.
- **Scripted scan retries across multiple exchanges** (found from an actual smoke-test run: the LLM's first move for "discover all live hosts" was `ip addr show`, a reasonable check of its own network config before scanning — the original single-shot version parsed that as "0 hosts found" and silently proceeded with an empty pool, no error). Fixed by looping the scan up to 4 exchanges with a follow-up nudge on an empty result, only raising once every attempt comes back empty. See `docs/features/rl-environment.md`'s "Scripted Initial Scan" section.
- **`rl/parser.py`'s nmap sub-parser excludes hosts with no reverse-DNS hostname** (also found from the same smoke-test run, once the scan retry fix above let it actually run `nmap`): the Docker bridge gateway (`172.21.0.1` in that run) got swept into the discovered pool alongside real containers, and — having no exploit path and never being removed from the pool — got re-engaged across 3 separate engagements, burning 7 of 20 total steps on an address that can never yield anything. Real Compose containers always resolve to their service DNS name; the gateway doesn't, so an empty hostname is now treated as infrastructure noise and dropped rather than added to state. This is a pre-existing parser gap (predates this ADR), but single-host engagement makes it far more expensive than it was under per-step multi-host re-selection. See `docs/features/rl-parser.md`'s "nmap" section.
- **`scripts/train.py` refreshes `state` after `start_engagement()`.** Found in the same self-review pass as the normalization fix below: `start_engagement()` zeros `engagement_progress` for the newly active host directly in `EpisodeState`, but the episode loop's first `policy.sample(state, host_idx)` call of each engagement was still using the tensor snapshot from *before* that call — stale for `engagement_progress` specifically. Invisible on a host's first-ever engagement (an untouched row is already zero), but on a re-engagement (post-`ABANDON` or post-safety-cap) the policy's first decision saw the stale, often-near-1.0 value from the *previous* engagement's end, biasing toward premature `ABANDON` right as a fresh budget starts — the opposite of the feature's intent. `scripts/run_rl_episode.py`'s random policy was unaffected (it reads `host_features()` fresh every step rather than holding a tensor). Fixed with a `state = env._state.to_tensor()` refresh immediately after `start_engagement()`. See `docs/features/rl-training.md`.
- **`tried_*` counts normalized to 0..1 in `EpisodeState.to_tensor()`.** Found on a self-review after the try-count/engagement-progress state richness pass (2026-07-15) — `mark_tried()` widened `tried_*` from a 0/1 flag to a raw count capped at `MAX_TRIED_COUNT=5`, but `Policy._action_logits()` flattens the whole state tensor straight into `Linear→ReLU` with no normalization anywhere in `rl/`. Every other feature is 0/1 or already 0..1, so an unscaled count up to 5 would dominate those in the same dot product, skewing early training toward whatever host/action pair had been tried most rather than what the flags actually mean. Fixed at the tensor boundary (`to_tensor()` divides the `tried_*` columns by `MAX_TRIED_COUNT`) rather than at write time, since `mark_tried()`/`get()`/`host_features()` all still need the raw count. See `docs/features/rl-environment.md`'s state section.
