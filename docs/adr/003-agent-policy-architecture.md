# ADR 003 — Agent Policy Architecture

**Status: Adopted**

## Description

A hierarchical policy where a learned policy network guides a frozen LLM, which in turn generates shell commands executed in the sandbox. The LLM is never fine-tuned.

```
State → [Embedding network] → state representation
                                      ↓
                            [Policy network] → guidance/context
                                      ↓
                                    LLM → bash command
                                      ↓
                              Docker execution
                                      ↓
                                   Reward
```

The LLM's non-determinism is treated as environment stochasticity — the policy network learns to work with it rather than against it.

## Embedding Network

Encodes the history of actions, observations, and outcomes into a fixed-size state representation. Candidate approaches:

- **LSTM/GRU** — encodes the (action, observation, reward) sequence across a trajectory. Natural fit for the sequential nature of pentesting.
- **MLP over structured state vector** — fixed set of features (ports discovered, credentials found/failed, shell access obtained). Simpler to train early on.
- **Graph network** — encodes discovered network topology as a graph with learned node/edge features. Most expressive, most complex.

## Policy Network

Outputs guidance that steers the LLM. Candidate output types:

- **Discrete category** (recon / exploit / post-exploit) — templated into the LLM prompt. Simplest, compatible with standard RL algorithms.
- **Continuous embedding** — projected into a soft prompt prefix. More expressive, harder to train through an API.
- **Natural language instruction** — generated text prepended to the system prompt. Most flexible, least structured.

## RL Algorithm

Actor-critic (PPO) is the natural fit. The policy network is the actor; the embedding network is the shared trunk between actor and critic heads.

## Known Concern: Inference Latency at RL Scale

Initial testing with `moonshotai/Kimi-K2.6` via DeepInfra recorded ~15s per inference call. At RL training scale this compounds significantly — 1000 steps ≈ 4 hours of inference time alone, before accounting for Docker execution or reward computation.

Candidate mitigations (to evaluate when designing the training loop):

- **Async parallel episodes** — run multiple environment instances concurrently with parallel API calls. Likely the highest gain per unit of effort.
- **Smaller model for training** — use a faster model during RL exploration, benchmark with K2.6 at evaluation time. Requires verifying tool-use quality doesn't degrade unacceptably.
- **Local model** — eliminates network latency entirely; requires GPU and may reduce tool-use quality.

Not a blocker for early experiments. Revisit when episode length and batch size are known.

## State, Action, and Reward Specification

The concrete representation of state, action, and reward that this policy network operates on is specified in ADR 006.

## Why Not Adopted Yet

The architecture is well-motivated but adds significant complexity before the basic agent loop is proven. Revisit once a simpler baseline (direct LLM prompting) has been benchmarked.