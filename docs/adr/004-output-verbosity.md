# ADR 004 — Output Verbosity and Length Management

## Decision

Two-layer approach: minimal verbosity as a system prompt instruction (primary), hard truncation as a safety net (fallback).

**Layer 1 — System prompt instruction:**
The LLM is instructed to use the minimum verbosity needed for the task. Specific guidance:
- `nmap`: avoid `-v`/`-vv`; prefer structured output formats (`-oG` or `-oX`) over plain text where applicable.
- `hydra`: avoid `-V` (per-attempt verbose output).
- General: never use verbose flags unless the task explicitly requires them.

**Layer 2 — Hard truncation:**
If a command still produces output exceeding the configured limit, output is truncated using a start + end strategy: the first half and last half of the limit are preserved and the middle is dropped. This keeps both the command header (printed first by most tools) and the final result (printed last by tools like hydra).

Truncation is indicated explicitly in the tool result so the LLM knows output was cut.

## Rationale

Hard truncation alone is insufficient because tools like hydra print found credentials at the end of their output — tail truncation would discard exactly the information the LLM needs. Addressing verbosity at the prompt level is the cleaner solution; truncation handles edge cases where the LLM ignores the instruction or output is unexpectedly large.

The system prompt instruction also keeps context windows clean across an entire episode, reducing token costs at scale — not just a safety concern but a practical one for RL training.