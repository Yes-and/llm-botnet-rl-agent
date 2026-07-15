"""
RL environment: Gym-like reset()/interact() wrapper for ADR 014 Phase 1
(single-host engagement, worker only — host selection is not learned yet).

The RL policy picks an Action against the currently active host; the environment
translates that into a natural-language instruction, calls the LLM to get a shell
command, executes it, parses the output, updates episode state, and returns
(obs, reward, done, info).

agent/ and rl/ are intentionally decoupled — this module imports from agent/,
but nothing in agent/ imports from rl/.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import openai
import torch

from agent.executor import Executor, format_tool_result
from agent.llm_client import LLMClient, build_initial_messages
from rl.actions import Action
from rl.parser import parse_step
from rl.reward import RewardCalculator
from rl.state import EpisodeState

logger = logging.getLogger(__name__)
transcript_logger = logging.getLogger("rl.transcript")  # human-readable per-step audit stream

_TRANSCRIPT_OUTPUT_CHARS = 600  # snippet length in the transcript; full output is in messages/debug log

_INITIAL_TASK = (
    "You are operating on a private internal network. "
    "Your objective is to discover and exploit all vulnerable services. "
    "You will be given one specific task per step — focus only on that task."
)

# Auto-run once at reset() to populate the host pool — not a learned action (ADR 014:
# recon-of-the-subnet is not the capability under test). Still LLM-driven rather than
# a hardcoded nmap invocation: the environment has no notion of the sandbox's subnet
# CIDR, and the LLM already knows how to discover its own network. What's scripted is
# *that* it runs, unconditionally, before any policy decision — not *how* it runs.
_INITIAL_SCAN_INSTRUCTION = "Discover all live hosts on the local subnet."
_INITIAL_SCAN_FOLLOWUP = (
    "That did not discover any live hosts yet. Run the actual network scan "
    "(e.g. nmap) against your subnet now to find them."
)
# A reasonable agent's first move for "discover the subnet" is often checking its own
# network config (e.g. `ip addr`) before running the actual scan — that's expected
# behavior, not a failure, so a single exchange isn't enough to call this done or failed.
_INITIAL_SCAN_MAX_TRIES = 4

# Single source of truth for the default context_window — scripts/train.py imports this
# rather than hardcoding its own number, so the two can't silently drift apart.
DEFAULT_CONTEXT_WINDOW = 3


@dataclass
class EnvironmentConfig:
    container_name: str
    max_steps: int = 40
    max_engagement_steps: int = 10  # safety cap: ceiling per engagement, not a target length (ADR 014)
    dry_run: bool = False
    timeout: int = 60
    max_output_chars: int = 4000
    model: str = "moonshotai/Kimi-K2.6"
    context_window: int = DEFAULT_CONTEXT_WINDOW
    api_timeout: int = 60
    reasoning_effort: str | None = None  # e.g. "none" to disable thinking on Qwen models


class Environment:
    """
    Gym-like RL environment for the attacker agent (ADR 014 Phase 1).

    Usage::

        env = Environment(config)
        obs = env.reset()                       # also runs the scripted host-discovery scan
        env.start_engagement(host_ip)            # picked externally — host selection isn't learned yet
        obs, reward, done, info = env.interact(Action.SCAN_PORTS)
        # ... keep calling interact() until info["engagement_done"] ...
    """

    def __init__(self, config: EnvironmentConfig) -> None:
        self.config = config
        self._state = EpisodeState()
        self._reward_calc = RewardCalculator()
        self._client = LLMClient(model=config.model, api_timeout=config.api_timeout, reasoning_effort=config.reasoning_effort)
        self._executor = Executor(
            config.container_name,
            dry_run=config.dry_run,
            timeout=config.timeout,
            max_output_chars=config.max_output_chars,
        )
        self._messages: list[dict] = []
        self._step_count: int = 0
        self._active_host: str | None = None
        self._engagement_step_count: int = 0

    # ── Public interface ──────────────────────────────────────────────────────

    def reset(self) -> torch.Tensor:
        """Start a new episode: clears state, then runs the scripted initial scan
        to populate the host pool. Returns the resulting observation."""
        if not self.config.dry_run:
            probe = self._executor.execute("echo ok")
            if probe.exit_code != 0:
                raise RuntimeError(
                    f"Container '{self.config.container_name}' is not reachable "
                    f"(exit={probe.exit_code}). Is the sandbox running?"
                )
        self._state.reset()
        self._reward_calc.reset()
        self._messages = build_initial_messages(_INITIAL_TASK)
        self._n_header = len(self._messages)
        self._step_count = 0
        self._active_host = None
        self._engagement_step_count = 0
        logger.info("=== Episode reset ===")
        self._scripted_initial_scan()
        return self._state.to_tensor()

    @property
    def active_host(self) -> str | None:
        return self._active_host

    def start_engagement(self, host_ip: str) -> None:
        """Set the active host for the next sequence of interact() calls."""
        if host_ip not in self._state.known_hosts():
            raise ValueError(f"Cannot start engagement on unknown host {host_ip!r}")
        self._active_host = host_ip
        self._engagement_step_count = 0

    def interact(self, action: Action) -> tuple[torch.Tensor, float, bool, dict[str, Any]]:
        """
        Execute one interaction step against the currently active host (set via
        start_engagement()). Returns the standard (obs, reward, done, info) Gym
        contract — `done` is the episode-level signal (global max_steps).

        info["engagement_done"] is True when this step ended the current engagement
        — an ExploitEvent fired, ABANDON was sampled, or the per-engagement safety
        cap was hit. The caller should return to host selection when set. A skipped
        try (LLM error, timeout) does NOT end the engagement — the same host stays
        active for the next interaction step, though it still counts against the
        safety cap.
        """
        if self._active_host is None:
            raise RuntimeError("interact() called with no active engagement — call start_engagement() first")
        ip = self._active_host

        if action == Action.ABANDON:
            reward = self._reward_calc.step()
            self._step_count += 1
            self._engagement_step_count += 1
            done = self._step_count >= self.config.max_steps
            logger.info(
                "[Step %2d/%d] %-20s → %-16s  hosts=%d  reward=%+.1f",
                self._step_count, self.config.max_steps, action.name, ip, len(self._state.known_hosts()), reward,
            )
            self._log_transcript(action, ip, reward, command="(abandon — no command issued)")
            self._active_host = None
            return self._state.to_tensor(), reward, done, {
                "step": self._step_count, "action": action.name, "host": ip,
                "exploit": None, "engagement_done": True,
            }

        reward, info = self._try_once(action, ip)
        self._engagement_step_count += 1
        done = self._step_count >= self.config.max_steps

        exploited = info.get("exploit") is not None
        if exploited:
            self._state.remove(ip)

        cap_hit = self._engagement_step_count >= self.config.max_engagement_steps
        engagement_done = exploited or cap_hit
        if engagement_done:
            self._active_host = None

        return self._state.to_tensor(), reward, done, {**info, "engagement_done": engagement_done}

    def _scripted_initial_scan(self) -> None:
        """Auto-run at reset() to populate the host pool. Not a policy decision and
        not part of the step budget or reward (ADR 014).

        Loops up to _INITIAL_SCAN_MAX_TRIES exchanges rather than a single shot: a
        reasonable agent's first move for "discover the subnet" is often checking
        its own network config before running the actual scan, so one exchange
        frequently finds zero hosts without anything being wrong — nudge with a
        follow-up and try again. Only raises once every attempt has found nothing;
        that's a hard error, not a skip, since an empty pool means no engagement is
        possible for the whole episode and absorbing the failure would silently
        waste the run.
        """
        instruction = _INITIAL_SCAN_INSTRUCTION
        for attempt in range(1, _INITIAL_SCAN_MAX_TRIES + 1):
            self._messages.append({"role": "user", "content": instruction})
            try:
                request = self._client.complete(self._messages)
            except (ValueError, openai.APITimeoutError) as exc:
                raise RuntimeError(f"Scripted initial scan failed: {exc}") from exc
            self._messages.append(request.assistant_message)
            result = self._executor.execute(request.command)
            self._messages.append({
                "role": "tool",
                "tool_call_id": request.tool_call_id,
                "content": format_tool_result(result),
            })
            parsed = parse_step(request.command, result.output, result.exit_code)
            for host_ip, features in parsed.state_updates:
                self._state.update(host_ip, features)

            if self._state.known_hosts():
                logger.info(
                    "Initial scan: discovered %d host(s) (attempt %d/%d)",
                    len(self._state.known_hosts()), attempt, _INITIAL_SCAN_MAX_TRIES,
                )
                return
            instruction = _INITIAL_SCAN_FOLLOWUP

        raise RuntimeError(
            f"Scripted initial scan found no hosts after {_INITIAL_SCAN_MAX_TRIES} attempts. "
            "Check train.debug.log for the commands the LLM ran — this usually means a bad "
            "subnet guess or the sandbox network isn't reachable, not an LLM API failure."
        )

    def _try_once(self, action: Action, ip: str) -> tuple[float, dict[str, Any]]:
        """Execute a single primitive command for (action, ip). Returns (reward, info)."""
        instruction = _action_to_instruction(action, ip)
        self._messages.append({"role": "user", "content": instruction})

        try:
            request = self._client.complete(self._messages)
        except (ValueError, openai.APITimeoutError) as exc:
            self._messages.pop()  # remove the dangling instruction; keeps history well-formed
            logger.warning("LLM call failed, skipping step: %s", exc)
            reward = self._reward_calc.step()
            self._step_count += 1
            skip = "api_timeout" if isinstance(exc, openai.APITimeoutError) else "no_tool_call"
            logger.info(
                "[Step %2d/%d] %-20s → %-16s  hosts=%d  reward=%+.1f  skip=%s",
                self._step_count, self.config.max_steps,
                action.name, ip, len(self._state.known_hosts()), reward, skip,
            )
            self._log_transcript(action, ip, reward, skip=skip)
            return reward, {"step": self._step_count, "skip": skip}
        except Exception as exc:
            self._messages.pop()
            logger.exception("Unexpected error in interact(), skipping: %s", exc)
            reward = self._reward_calc.step()
            self._step_count += 1
            logger.info(
                "[Step %2d/%d] %-20s → %-16s  hosts=%d  reward=%+.1f  skip=unexpected_error",
                self._step_count, self.config.max_steps,
                action.name, ip, len(self._state.known_hosts()), reward,
            )
            self._log_transcript(action, ip, reward, skip="unexpected_error")
            return reward, {"step": self._step_count, "skip": "unexpected_error"}

        self._messages.append(request.assistant_message)

        result = self._executor.execute(request.command)
        self._messages.append({
            "role": "tool",
            "tool_call_id": request.tool_call_id,
            "content": format_tool_result(result),
        })
        self._prune_messages()

        parsed = parse_step(request.command, result.output, result.exit_code)

        # Snapshot shell_access BEFORE applying state updates. All exploit-emitting
        # sub-parsers include {"shell_access": True} in state_updates, so if we check
        # after the update the host always looks already-exploited.
        already_exploited = (
            parsed.exploit is not None
            and self._state.get(parsed.exploit.host, "shell_access")
        )

        for host_ip, features in parsed.state_updates:
            self._state.update(host_ip, features)

        self._state.mark_tried(ip, action)

        # The LLM's command can name any host regardless of which one is currently
        # active (parsed.exploit.host comes from a regex over the command string, not
        # from `ip`). Crediting that to the action the policy actually sampled would
        # reinforce the wrong pair for a result it didn't cause — so an exploit only
        # counts here if it landed on the host this engagement is targeting.
        wrong_host = parsed.exploit is not None and parsed.exploit.host != ip
        exploit = None if (parsed.exploit is None or already_exploited or wrong_host) else parsed.exploit
        reward = self._reward_calc.step(exploit)
        self._step_count += 1

        info: dict[str, Any] = {
            "step": self._step_count,
            "action": action.name,
            "host": ip,
            "command": request.command,
            "exit_code": result.exit_code,
            "exploit": exploit,
            "truncated": result.truncated,
        }
        logger.debug("cmd=%r exit=%d truncated=%s", request.command, result.exit_code, result.truncated)
        if wrong_host:
            logger.info(
                "  └─ exploit on %s ignored — engagement targeted %s (action=%s)",
                parsed.exploit.host, ip, action.name,
            )
        logger.info(
            "[Step %2d/%d] %-20s → %-16s  hosts=%d  reward=%+.1f%s",
            self._step_count, self.config.max_steps,
            action.name, ip, len(self._state.known_hosts()), reward,
            f"  exploit={exploit.vulnerability}" if exploit else "",
        )
        self._log_transcript(
            action, ip, reward,
            reasoning=request.reasoning, content=request.assistant_message["content"],
            command=request.command, output=result.output, exploit=exploit,
        )
        return reward, info

    def _log_transcript(
        self, action: Action, ip: str, reward: float, *,
        reasoning: str = "", content: str = "", command: str = "",
        output: str = "", exploit: Any = None, skip: str | None = None,
    ) -> None:
        """Emit one human-readable block to the transcript stream, pairing the RL
        action with what the LLM actually reasoned and ran — the artifact for
        auditing action/command mismatch."""
        if skip:
            transcript_logger.info(
                "── Step %d  action=%s  host=%s  reward=%+.1f  SKIP=%s",
                self._step_count, action.name, ip, reward, skip,
            )
            return
        thinking = reasoning or content or "(none)"
        snippet = output if len(output) <= _TRANSCRIPT_OUTPUT_CHARS else output[:_TRANSCRIPT_OUTPUT_CHARS] + " …[truncated]"
        exploit_str = f"  exploit={exploit.vulnerability}" if exploit else ""
        transcript_logger.info(
            "── Step %d  action=%s  host=%s  reward=%+.1f%s\n"
            "   thinking: %s\n   command: %s\n   output: %s",
            self._step_count, action.name, ip, reward, exploit_str,
            thinking, command, snippet,
        )

    @property
    def step_count(self) -> int:
        return self._step_count

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _prune_messages(self) -> None:
        """Trim message history to the last context_window complete exchanges."""
        variable = self._messages[self._n_header:]
        max_msgs = self.config.context_window * 3
        if len(variable) > max_msgs:
            self._messages = self._messages[:self._n_header] + variable[-max_msgs:]

# ── Action → natural-language instruction ────────────────────────────────────

def _action_to_instruction(action: Action, ip: str) -> str:
    match action:
        case Action.SCAN_PORTS:
            return f"Scan the most common ports on {ip} to identify open services."
        case Action.PROBE_PORT:
            return f"Run a service-version scan on all open ports of {ip}."
        case Action.BRUTE_FORCE_SSH:
            return f"Attempt to brute-force SSH credentials on {ip}."
        case Action.BRUTE_FORCE_FTP:
            return f"Attempt to brute-force FTP credentials on {ip}."
        case Action.BRUTE_FORCE_TELNET:
            return f"Attempt to brute-force Telnet credentials on {ip}."
        case Action.CONNECT_SSH:
            return (
                f"Attempt to log in via SSH to {ip} using any credentials you have "
                "discovered. Run 'id' to confirm shell access."
            )
        case Action.CONNECT_FTP:
            return f"Connect to the FTP service on {ip} and list directory contents."
        case Action.CONNECT_TELNET:
            return f"Check whether the Telnet service on {ip} is reachable and shows a login prompt."
        case Action.PROBE_HTTP:
            return f"Fetch the main page or HTTP headers from {ip}."
        case Action.PROBE_REDIS:
            return f"Connect to the Redis service on {ip} and run the INFO command."
        case Action.PROBE_MONGO:
            return f"Connect to the MongoDB service on {ip} and list available databases."
        case _:
            return f"Proceed with the next logical step against {ip}."
