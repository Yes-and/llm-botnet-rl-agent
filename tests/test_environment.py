"""
Environment tests. LLM and Docker interactions are mocked; all tests run offline.

The cases here focus on logic that fails silently: reward deduplication, host
resolution, coverage tracking, and episode termination. Correctness of
parse_step() itself is covered separately in test_parser.py.
"""

from unittest.mock import patch

import pytest
import torch

from agent.executor import CommandResult
from agent.llm_client import CommandRequest
from rl.actions import Action
from rl.environment import Environment, EnvironmentConfig
from rl.state import MAX_HOSTS, NUM_FEATURES


# ── Helpers ───────────────────────────────────────────────────────────────────

def _req(command: str) -> CommandRequest:
    return CommandRequest(
        command=command,
        tool_call_id="tc_test",
        assistant_message={"role": "assistant", "content": None},
    )


def _res(output: str = "", exit_code: int = 0) -> CommandResult:
    return CommandResult(
        command="",
        output=output,
        exit_code=exit_code,
        truncated=False,
        dry_run=False,
    )


@pytest.fixture
def env_mocks():
    with patch("rl.environment.LLMClient") as mock_llm_cls, \
         patch("rl.environment.Executor") as mock_exec_cls:
        mock_llm = mock_llm_cls.return_value
        mock_exec = mock_exec_cls.return_value
        mock_exec.execute.return_value = _res()  # exit_code=0 satisfies reachability probe
        env = Environment(EnvironmentConfig(container_name="test", max_steps=3))
        env.reset()  # initialise _n_header and message history
        mock_exec.reset_mock()  # clear probe call so tests start with clean call history
        yield env, mock_llm, mock_exec


# ── reset() ───────────────────────────────────────────────────────────────────

def test_reset_returns_zero_tensor(env_mocks):
    env, _, _ = env_mocks
    obs = env.reset()
    assert isinstance(obs, torch.Tensor)
    assert obs.shape == (MAX_HOSTS, NUM_FEATURES)
    assert obs.sum().item() == 0.0


def test_reset_clears_state_from_previous_episode(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True})
    assert env._state.known_hosts() != []
    obs = env.reset()
    assert env._state.known_hosts() == []
    assert obs.sum().item() == 0.0


# ── step(): state updates ─────────────────────────────────────────────────────

def test_broadcast_step_discovers_host(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    mock_llm.complete.return_value = _req("nmap -sn 172.18.0.0/24 -oG -")
    mock_exec.execute.return_value = _res("Host: 172.18.0.5 ()\tStatus: Up\n")

    obs, reward, _, info = env.step(Action.SCAN_NETWORK, 0)

    assert "172.18.0.5" in env._state.known_hosts()
    assert reward == pytest.approx(-0.1)
    assert info["exploit"] is None


def test_per_host_step_marks_tried(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True})
    mock_llm.complete.return_value = _req("nmap -sV -p 22 172.18.0.5 -oG -")
    mock_exec.execute.return_value = _res("")

    env.step(Action.SCAN_PORTS, 0)

    assert env._state.get("172.18.0.5", "tried_scan_ports")


# ── step(): reward ────────────────────────────────────────────────────────────

def test_exploit_step_gives_positive_reward(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True, "port_6379_open": True})
    mock_llm.complete.return_value = _req("redis-cli -h 172.18.0.5 INFO")
    mock_exec.execute.return_value = _res("# Server\nredis_version:7.0\n")

    _, reward, _, info = env.step(Action.PROBE_REDIS, 0)

    assert reward == pytest.approx(9.9)
    assert info["exploit"] is not None
    assert info["exploit"].vulnerability == "redis_no_auth"


def test_non_exploit_step_gives_step_penalty(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    mock_llm.complete.return_value = _req("nmap -sn 172.18.0.0/24 -oG -")
    mock_exec.execute.return_value = _res("")

    _, reward, _, _ = env.step(Action.SCAN_NETWORK, 0)

    assert reward == pytest.approx(-0.1)


# ── step(): deduplication ─────────────────────────────────────────────────────

def test_second_exploit_on_same_host_gives_no_reward(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True, "port_6379_open": True})
    mock_llm.complete.return_value = _req("redis-cli -h 172.18.0.5 INFO")
    mock_exec.execute.return_value = _res("# Server\nredis_version:7.0\n")

    _, reward1, _, info1 = env.step(Action.PROBE_REDIS, 0)
    _, reward2, _, info2 = env.step(Action.PROBE_REDIS, 0)

    assert reward1 == pytest.approx(9.9)
    assert info1["exploit"] is not None
    assert reward2 == pytest.approx(-0.1)
    assert info2["exploit"] is None


# ── step(): edge cases ────────────────────────────────────────────────────────

def test_invalid_host_idx_skips_without_llm_call(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    # No hosts discovered; host_idx=0 is out of range for a per-host action
    _, reward, _, info = env.step(Action.SCAN_PORTS, 0)

    mock_llm.complete.assert_not_called()
    assert info.get("skip") == "invalid_host_idx"
    assert reward == pytest.approx(-0.1)


def test_no_tool_call_skips_without_executing(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    mock_llm.complete.side_effect = ValueError("no tool call")

    _, reward, _, info = env.step(Action.SCAN_NETWORK, 0)

    mock_exec.execute.assert_not_called()
    assert info.get("skip") == "no_tool_call"
    assert reward == pytest.approx(-0.1)


# ── step(): done condition ────────────────────────────────────────────────────

def test_done_triggers_at_max_steps(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    mock_llm.complete.return_value = _req("nmap -sn 172.18.0.0/24 -oG -")
    mock_exec.execute.return_value = _res("")

    # max_steps=3; steps 1 and 2 should not be done
    for _ in range(2):
        _, _, done, _ = env.step(Action.SCAN_NETWORK, 0)
        assert not done

    _, _, done, _ = env.step(Action.SCAN_NETWORK, 0)
    assert done


def test_step_count_increments(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    mock_llm.complete.return_value = _req("nmap -sn 172.18.0.0/24 -oG -")
    mock_exec.execute.return_value = _res("")

    assert env.step_count == 0
    env.step(Action.SCAN_NETWORK, 0)
    assert env.step_count == 1
    env.step(Action.SCAN_NETWORK, 0)
    assert env.step_count == 2


# ── step_block(): early exit ──────────────────────────────────────────────────

def test_step_block_stops_early_on_exploit(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True, "port_6379_open": True})
    mock_llm.complete.return_value = _req("redis-cli -h 172.18.0.5 INFO")
    mock_exec.execute.return_value = _res("# Server\nredis_version:7.0\n")

    _, block_reward, _, info = env.step_block(Action.PROBE_REDIS, 0, max_tries=5)

    assert info["tries_used"] == 1
    assert mock_exec.execute.call_count == 1
    assert block_reward == pytest.approx(9.9)
    assert info["exploit"] is not None
    assert info["exploit"].vulnerability == "redis_no_auth"


def test_step_block_stops_early_on_creds_found(env_mocks):
    """BRUTE_FORCE_SSH never emits an ExploitEvent — the block should stop as soon as
    creds_found flips true, not run to max_tries."""
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True, "port_22_open": True})
    cmd = _req("hydra -l admin -P /usr/share/wordlists/passwords.txt ssh://172.18.0.5")
    mock_llm.complete.side_effect = [cmd, cmd]
    mock_exec.execute.side_effect = [
        _res("1 of 1 target completed, 0 valid passwords found\n"),
        _res("[22][ssh] host: 172.18.0.5   login: admin   password: admin123\n"),
    ]

    _, block_reward, _, info = env.step_block(Action.BRUTE_FORCE_SSH, 0, max_tries=5)

    assert info["tries_used"] == 2
    assert mock_exec.execute.call_count == 2
    assert env._state.get("172.18.0.5", "creds_found")
    assert block_reward == pytest.approx(-0.2)  # two step penalties, no exploit reward
    assert info["exploit"] is None


def test_step_block_exhausts_when_no_progress(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True, "port_22_open": True})
    mock_llm.complete.return_value = _req("hydra -l admin -P /usr/share/wordlists/passwords.txt ssh://172.18.0.5")
    mock_exec.execute.return_value = _res("1 of 1 target completed, 0 valid passwords found\n")

    # env_mocks' max_steps=3; use max_tries=2 so this test isn't confounded by hitting done.
    _, block_reward, _, info = env.step_block(Action.BRUTE_FORCE_SSH, 0, max_tries=2)

    assert info["tries_used"] == 2
    assert mock_exec.execute.call_count == 2
    assert not env._state.get("172.18.0.5", "creds_found")
    assert block_reward == pytest.approx(-0.2)


# ── step_block(): skip handling ───────────────────────────────────────────────

def test_step_block_skip_on_first_try_reports_block_skip(env_mocks):
    """Matches step()'s existing single-try skip behaviour exactly — nothing executed,
    whole block reported as skip so training excludes it."""
    env, mock_llm, mock_exec = env_mocks
    mock_llm.complete.side_effect = ValueError("no tool call")

    _, block_reward, _, info = env.step_block(Action.SCAN_NETWORK, 0, max_tries=5)

    mock_exec.execute.assert_not_called()
    assert info.get("skip") == "no_tool_call"
    assert info["tries_used"] == 1
    assert block_reward == pytest.approx(-0.1)


def test_step_block_skip_after_real_tries_keeps_earned_reward(env_mocks):
    """A skip on a later try should not erase the reward already earned, and the block
    should NOT be reported as a top-level skip — real tries happened, so it should
    still be trained on."""
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True, "port_22_open": True})
    real_cmd = _req("hydra -l admin -P /usr/share/wordlists/passwords.txt ssh://172.18.0.5")
    mock_llm.complete.side_effect = [real_cmd, ValueError("no tool call")]
    mock_exec.execute.return_value = _res("1 of 1 target completed, 0 valid passwords found\n")

    _, block_reward, _, info = env.step_block(Action.BRUTE_FORCE_SSH, 0, max_tries=5)

    assert info.get("skip") is None
    assert info["tries_used"] == 2
    assert mock_exec.execute.call_count == 1
    assert block_reward == pytest.approx(-0.2)  # one real try + one skip, both -0.1


# ── step_block(): step budget ─────────────────────────────────────────────────

def test_step_block_charges_real_tries_against_step_budget(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True, "port_22_open": True})
    mock_llm.complete.return_value = _req("hydra -l admin -P /usr/share/wordlists/passwords.txt ssh://172.18.0.5")
    mock_exec.execute.return_value = _res("1 of 1 target completed, 0 valid passwords found\n")

    assert env.step_count == 0
    _, _, done, info = env.step_block(Action.BRUTE_FORCE_SSH, 0, max_tries=2)

    assert env.step_count == 2  # both tries counted against the budget, not 1 per block
    assert info["tries_used"] == 2
    assert not done  # env_mocks' max_steps=3


# ── step(): equivalence to step_block(..., max_tries=1) ───────────────────────

def test_step_equals_step_block_of_one(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True, "port_6379_open": True})
    mock_llm.complete.return_value = _req("redis-cli -h 172.18.0.5 INFO")
    mock_exec.execute.return_value = _res("# Server\nredis_version:7.0\n")

    _, reward, _, info = env.step(Action.PROBE_REDIS, 0)

    assert reward == pytest.approx(9.9)
    assert info["tries_used"] == 1
