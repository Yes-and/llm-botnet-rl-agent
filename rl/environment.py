"""
RL environment: Gym-like reset()/step() wrapper.

The RL policy picks (Action, host_idx); the environment translates that into a
natural-language instruction, calls the LLM to get a shell command, executes it,
parses the output, updates episode state, and returns (obs, reward, done, info).

agent/ and rl/ are intentionally decoupled — this module imports from agent/,
but nothing in agent/ imports from rl/.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import torch

from agent.executor import Executor, format_tool_result
from agent.llm_client import LLMClient, build_initial_messages
from rl.actions import Action, BROADCAST_ACTIONS
from rl.parser import parse_step
from rl.reward import RewardCalculator
from rl.state import EpisodeState

logger = logging.getLogger(__name__)

_INITIAL_TASK = (
    "You are operating on a private internal network. "
    "Your objective is to discover and exploit all vulnerable services. "
    "You will be given one specific task per step — focus only on that task."
)


@dataclass
class EnvironmentConfig:
    container_name: str
    max_steps: int = 40
    dry_run: bool = False
    timeout: int = 60
    max_output_chars: int = 4000
    model: str = "moonshotai/Kimi-K2.6"
    context_window: int = 10
    api_timeout: int = 60


class Environment:
    """
    Gym-like RL environment for the attacker agent.

    Usage::

        env = Environment(config)
        obs = env.reset()
        obs, reward, done, info = env.step(Action.SCAN_NETWORK, host_idx=0)
    """

    def __init__(self, config: EnvironmentConfig) -> None:
        self.config = config
        self._state = EpisodeState()
        self._reward_calc = RewardCalculator()
        self._client = LLMClient(model=config.model, api_timeout=config.api_timeout)
        self._executor = Executor(
            config.container_name,
            dry_run=config.dry_run,
            timeout=config.timeout,
            max_output_chars=config.max_output_chars,
        )
        self._messages: list[dict] = []
        self._step_count: int = 0

    # ── Public interface ──────────────────────────────────────────────────────

    def reset(self) -> torch.Tensor:
        """Start a new episode. Returns the initial observation (all zeros)."""
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
        logger.info("=== Episode reset ===")
        return self._state.to_tensor()

    @staticmethod
    def _host_label(action: Action, ip: str | None) -> str:
        if action == Action.DO_NOTHING:
            return "no_host"
        if action == Action.SCAN_NETWORK:
            return "all_hosts"
        return ip or "unknown"

    def step(
        self, action: Action, host_idx: int
    ) -> tuple[torch.Tensor, float, bool, dict[str, Any]]:
        """
        Execute one RL step.

        action   — the Action enum value chosen by the policy
        host_idx — index into state.known_hosts() (ignored for broadcast actions)

        Returns (obs, reward, done, info).
        info keys: step, action, host, command, exit_code, exploit, truncated
        """
        ip = self._resolve_host(action, host_idx)
        if ip is None and action not in BROADCAST_ACTIONS:
            # host_idx out of range — skip gracefully
            reward = self._reward_calc.step()
            self._step_count += 1
            done = self._step_count >= self.config.max_steps
            logger.info(
                "[Step %2d/%d] %-20s → %-16s  hosts=%d  reward=%+.1f  skip=invalid_host_idx",
                self._step_count, self.config.max_steps,
                action.name, self._host_label(action, ip), len(self._state.known_hosts()), reward,
            )
            return self._state.to_tensor(), reward, done, {
                "step": self._step_count,
                "skip": "invalid_host_idx",
            }

        instruction = _action_to_instruction(action, ip)
        self._messages.append({"role": "user", "content": instruction})

        try:
            request = self._client.complete(self._messages)
        except ValueError as exc:
            self._messages.pop()  # remove the dangling instruction; keeps history well-formed
            logger.debug("LLM produced no tool call: %s", exc)
            reward = self._reward_calc.step()
            self._step_count += 1
            done = self._step_count >= self.config.max_steps
            logger.info(
                "[Step %2d/%d] %-20s → %-16s  hosts=%d  reward=%+.1f  skip=no_tool_call",
                self._step_count, self.config.max_steps,
                action.name, self._host_label(action, ip), len(self._state.known_hosts()), reward,
            )
            return self._state.to_tensor(), reward, done, {
                "step": self._step_count,
                "skip": "no_tool_call",
            }

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

        if ip is not None:
            self._state.mark_tried(ip, action)

        exploit = None if (parsed.exploit is None or already_exploited) else parsed.exploit
        reward = self._reward_calc.step(exploit)
        self._step_count += 1
        done = self._step_count >= self.config.max_steps

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
        logger.info(
            "[Step %2d/%d] %-20s → %-16s  hosts=%d  reward=%+.1f%s",
            self._step_count, self.config.max_steps,
            action.name, self._host_label(action, ip), len(self._state.known_hosts()), reward,
            f"  exploit={exploit.vulnerability}" if exploit else "",
        )
        return self._state.to_tensor(), reward, done, info

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

    def _resolve_host(self, action: Action, host_idx: int) -> str | None:
        if action in BROADCAST_ACTIONS:
            return None
        hosts = self._state.known_hosts()
        if host_idx < len(hosts):
            return hosts[host_idx]
        return None

# ── Action → natural-language instruction ────────────────────────────────────

def _action_to_instruction(action: Action, ip: str | None) -> str:
    match action:
        case Action.DO_NOTHING:
            return "Do nothing this step. Run 'echo ok' to acknowledge."
        case Action.SCAN_NETWORK:
            return "Discover all live hosts on the local subnet."
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
