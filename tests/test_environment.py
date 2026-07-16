"""
Environment tests. LLM and Docker interactions are mocked; all tests run offline.

The cases here focus on logic that fails silently: reward deduplication, host
resolution, coverage tracking, engagement/episode termination, and the scripted
initial scan. Correctness of parse_step() itself is covered separately in
test_parser.py.
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

def _req(command: str, reasoning: str = "") -> CommandRequest:
    return CommandRequest(
        command=command,
        tool_call_id="tc_test",
        reasoning=reasoning,
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
        # The scripted scan now retries until it finds a host (see
        # test_reset_retries_scan_until_hosts_found) rather than accepting an empty
        # result — give it one to find on the first try so fixture-level reset()
        # succeeds without looping, then remove it immediately so tests still start
        # from a clean, empty pool.
        mock_llm.complete.return_value = _req("nmap -sn 172.18.0.0/24 -oG -")
        mock_exec.execute.return_value = _res("Host: 172.18.0.250 (fixture_host)\tStatus: Up\n")
        env = Environment(EnvironmentConfig(container_name="test", max_steps=5, max_engagement_steps=3))
        env.reset()
        env._state.remove("172.18.0.250")
        mock_exec.reset_mock()
        mock_llm.reset_mock()
        mock_exec.execute.return_value = _res()  # neutral default for per-test interact() calls
        yield env, mock_llm, mock_exec


# ── reset() / scripted initial scan ────────────────────────────────────────────

def test_reset_returns_correctly_shaped_tensor(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    mock_llm.complete.return_value = _req("nmap -sn 172.18.0.0/24 -oG -")
    mock_exec.execute.return_value = _res("Host: 172.18.0.5 (target_host)\tStatus: Up\n")

    obs = env.reset()

    assert isinstance(obs, torch.Tensor)
    assert obs.shape == (MAX_HOSTS, NUM_FEATURES)


def test_reset_clears_state_from_previous_episode(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True})
    assert "172.18.0.5" in env._state.known_hosts()

    mock_llm.complete.return_value = _req("nmap -sn 172.18.0.0/24 -oG -")
    mock_exec.execute.return_value = _res("Host: 172.18.0.77 (target_host)\tStatus: Up\n")
    env.reset()

    assert "172.18.0.5" not in env._state.known_hosts()  # stale state cleared, not merged
    assert "172.18.0.77" in env._state.known_hosts()      # fresh scan's result is what's there now


def test_reset_runs_scripted_scan_and_discovers_hosts(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    mock_llm.complete.return_value = _req("nmap -sn 172.18.0.0/24 -oG -")
    mock_exec.execute.return_value = _res("Host: 172.18.0.5 (target_host)\tStatus: Up\n")

    env.reset()

    assert "172.18.0.5" in env._state.known_hosts()
    mock_llm.complete.assert_called_once()


def test_reset_retries_scan_until_hosts_found(env_mocks):
    """A reasonable agent's first move for 'discover the subnet' is often checking
    its own network config before running the actual scan — that must not be
    treated as a failed/empty scan. Reproduces the exact shape of a real run: first
    exchange is `ip addr`, second is the actual nmap call."""
    env, mock_llm, mock_exec = env_mocks
    mock_llm.complete.side_effect = [
        _req("ip addr show | grep -A2 'inet '"),
        _req("nmap -sn 172.18.0.0/24 -oG -"),
    ]
    mock_exec.execute.side_effect = [
        _res(),                                                    # echo ok reachability probe
        _res("eth0: inet 172.18.0.50/24 ..."),                     # scan attempt 1 — no host match
        _res("Host: 172.18.0.60 (target_host)\tStatus: Up\n"),     # scan attempt 2 — the actual scan
    ]

    env.reset()

    assert "172.18.0.60" in env._state.known_hosts()
    assert mock_llm.complete.call_count == 2


def test_reset_raises_when_scan_never_finds_hosts(env_mocks):
    """If every attempt comes back empty, that's a real problem (bad subnet guess,
    unreachable network) — must surface as a hard error, not silently proceed with
    an empty pool that would waste the whole episode."""
    env, mock_llm, mock_exec = env_mocks
    mock_llm.complete.return_value = _req("ip addr show")  # never actually scans
    mock_exec.execute.return_value = _res("eth0: inet 172.18.0.50/24 ...")

    with pytest.raises(RuntimeError, match="found no hosts"):
        env.reset()


def test_reset_raises_on_scan_llm_failure(env_mocks):
    """An empty pool from a swallowed scan failure would silently waste the whole
    episode — this must be a hard error, not a skip."""
    env, mock_llm, mock_exec = env_mocks
    mock_llm.complete.side_effect = ValueError("no tool call")

    with pytest.raises(RuntimeError, match="Scripted initial scan failed"):
        env.reset()


# ── start_engagement() ─────────────────────────────────────────────────────────

def test_start_engagement_sets_active_host(env_mocks):
    env, _, _ = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True})
    env.start_engagement("172.18.0.5")
    assert env.active_host == "172.18.0.5"


def test_start_engagement_unknown_host_raises(env_mocks):
    env, _, _ = env_mocks
    with pytest.raises(ValueError):
        env.start_engagement("172.18.0.99")


def test_start_engagement_resets_message_history(env_mocks):
    """Each engagement gets the LLM's full attention on one host, not a rolling
    window shared across the whole episode's unrelated hosts (the cause of a real
    bug — see rl/environment.py's start_engagement docstring). Compares against
    env._n_header (the actual source of truth) rather than a snapshot of
    env._messages — the fixture's own reset() already leaves a leftover scripted-
    scan exchange in _messages, so a snapshot taken before the first
    start_engagement() call would itself be polluted with content the reset is
    supposed to clear."""
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True})
    env._state.update("172.18.0.6", {"is_alive": True})

    env.start_engagement("172.18.0.5")
    assert len(env._messages) == env._n_header  # fixture's leftover scan exchange cleared too

    mock_llm.complete.return_value = _req("nmap -sV -p 22 172.18.0.5 -oG -")
    mock_exec.execute.return_value = _res("")
    env.interact(Action.SCAN_PORTS)
    assert len(env._messages) > env._n_header  # this engagement's exchange is present

    env.start_engagement("172.18.0.6")
    assert len(env._messages) == env._n_header  # prior engagement's exchange is gone


def test_engagement_step_count_increments_and_resets(env_mocks):
    """Policy.sample()'s ABANDON gate reads this property directly (not through
    the state tensor) — must increment per interact() call and reset on
    start_engagement(), independent of which host."""
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True})
    env._state.update("172.18.0.6", {"is_alive": True})

    env.start_engagement("172.18.0.5")
    assert env.engagement_step_count == 0

    mock_llm.complete.return_value = _req("nmap -sV -p 22 172.18.0.5 -oG -")
    mock_exec.execute.return_value = _res("")
    env.interact(Action.SCAN_PORTS)
    assert env.engagement_step_count == 1
    env.interact(Action.SCAN_PORTS)
    assert env.engagement_step_count == 2

    env.start_engagement("172.18.0.6")
    assert env.engagement_step_count == 0


def test_interact_without_active_engagement_raises(env_mocks):
    env, _, _ = env_mocks
    with pytest.raises(RuntimeError, match="no active engagement"):
        env.interact(Action.SCAN_PORTS)


# ── interact(): state updates, reward, attribution ─────────────────────────────

def test_interact_marks_tried(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True})
    env.start_engagement("172.18.0.5")
    mock_llm.complete.return_value = _req("nmap -sV -p 22 172.18.0.5 -oG -")
    mock_exec.execute.return_value = _res("")

    env.interact(Action.SCAN_PORTS)

    assert env._state.get("172.18.0.5", "tried_scan_ports")


def test_interact_non_exploit_gives_step_penalty(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True})
    env.start_engagement("172.18.0.5")
    mock_llm.complete.return_value = _req("nmap -sV -p 22 172.18.0.5 -oG -")
    mock_exec.execute.return_value = _res("")

    _, reward, _, _ = env.interact(Action.SCAN_PORTS)

    assert reward == pytest.approx(-0.1)


def test_interact_exploit_gives_reward_removes_host_and_ends_engagement(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True, "port_6379_open": True})
    env.start_engagement("172.18.0.5")
    mock_llm.complete.return_value = _req("redis-cli -h 172.18.0.5 INFO")
    mock_exec.execute.return_value = _res("# Server\nredis_version:7.0\n")

    _, reward, _, info = env.interact(Action.PROBE_REDIS)

    assert reward == pytest.approx(9.9)
    assert info["exploit"] is not None
    assert info["exploit"].vulnerability == "redis_no_auth"
    assert info["engagement_done"] is True
    assert "172.18.0.5" not in env._state.known_hosts()
    assert env.active_host is None


def test_interact_exploit_on_wrong_host_gives_no_reward_and_engagement_continues(env_mocks):
    """The LLM's command can name a different host than the active engagement — that
    must not be credited to the action the policy actually sampled, even though the
    exploit genuinely happened (state still reflects it on the host it hit)."""
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True, "port_6379_open": True})
    env.start_engagement("172.18.0.5")
    mock_llm.complete.return_value = _req("redis-cli -h 172.18.0.9 INFO")
    mock_exec.execute.return_value = _res("# Server\nredis_version:7.0\n")

    _, reward, _, info = env.interact(Action.PROBE_REDIS)

    assert reward == pytest.approx(-0.1)
    assert info["exploit"] is None
    assert info["engagement_done"] is False
    assert env._state.get("172.18.0.9", "shell_access")
    assert "172.18.0.5" in env._state.known_hosts()


def test_interact_exploit_wrong_action_gives_no_reward_but_still_ends_engagement(env_mocks):
    """Found in a real run (2026-07-16): the policy sampled BRUTE_FORCE_SSH, but the
    LLM ran a MongoDB connection instead (already knew Mongo was the only open
    service) and it succeeded. Real exploit, wrong action — no reward credit, but
    the host genuinely got compromised, so it's still removed and the engagement
    still ends (state realism is independent of RL credit assignment)."""
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True, "port_27017_open": True})
    env.start_engagement("172.18.0.5")
    mock_llm.complete.return_value = _req(
        "python3 -c \"from pymongo import MongoClient; "
        "client = MongoClient('mongodb://172.18.0.5:27017/'); print(client.list_database_names())\""
    )
    mock_exec.execute.return_value = _res("['admin', 'config', 'local']")

    _, reward, _, info = env.interact(Action.BRUTE_FORCE_SSH)

    assert reward == pytest.approx(-0.1)
    assert info["exploit"] is None
    assert info["compromised"] is True
    assert info["engagement_done"] is True
    assert "172.18.0.5" not in env._state.known_hosts()
    assert env.active_host is None


def test_start_engagement_on_solved_host_raises(env_mocks):
    """Once a host is removed from the pool, it can't be re-engaged — that IS the
    dedup mechanism now (replaces the old shell_access mask)."""
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True, "port_6379_open": True})
    env.start_engagement("172.18.0.5")
    mock_llm.complete.return_value = _req("redis-cli -h 172.18.0.5 INFO")
    mock_exec.execute.return_value = _res("# Server\nredis_version:7.0\n")
    env.interact(Action.PROBE_REDIS)

    with pytest.raises(ValueError):
        env.start_engagement("172.18.0.5")


# ── interact(): ABANDON ─────────────────────────────────────────────────────────

def test_abandon_ends_engagement_and_costs_step_penalty(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True})
    env.start_engagement("172.18.0.5")

    _, reward, _, info = env.interact(Action.ABANDON)

    assert reward == pytest.approx(-0.1)
    assert info["engagement_done"] is True
    assert env.active_host is None
    mock_llm.complete.assert_not_called()  # ABANDON is mechanical, not LLM-driven


def test_abandon_counts_against_step_budget(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True})
    env.start_engagement("172.18.0.5")

    assert env.step_count == 0
    env.interact(Action.ABANDON)
    assert env.step_count == 1


# ── interact(): safety cap ──────────────────────────────────────────────────────

def test_safety_cap_ends_engagement_without_exploit():
    with patch("rl.environment.LLMClient") as mock_llm_cls, \
         patch("rl.environment.Executor") as mock_exec_cls:
        mock_llm = mock_llm_cls.return_value
        mock_exec = mock_exec_cls.return_value
        mock_exec.execute.return_value = _res("Host: 172.18.0.250 (fixture_host)\tStatus: Up\n")
        mock_llm.complete.return_value = _req("nmap -sn 172.18.0.0/24 -oG -")
        env = Environment(EnvironmentConfig(container_name="test", max_steps=40, max_engagement_steps=2))
        env.reset()
        env._state.remove("172.18.0.250")
        env._state.update("172.18.0.5", {"is_alive": True, "port_22_open": True})
        env.start_engagement("172.18.0.5")
        mock_llm.complete.return_value = _req("hydra -l admin -P wordlist.txt ssh://172.18.0.5")
        mock_exec.execute.return_value = _res("1 of 1 target completed, 0 valid passwords found\n")

        _, _, _, info1 = env.interact(Action.BRUTE_FORCE_SSH)
        assert info1["engagement_done"] is False  # 1st of 2 allowed steps

        _, _, _, info2 = env.interact(Action.BRUTE_FORCE_SSH)
        assert info2["engagement_done"] is True  # cap hit
        assert info2["exploit"] is None
        assert env.active_host is None


# ── interact(): skip handling ───────────────────────────────────────────────────

def test_skip_does_not_end_engagement(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True})
    env.start_engagement("172.18.0.5")
    mock_llm.complete.side_effect = ValueError("no tool call")

    _, reward, _, info = env.interact(Action.SCAN_PORTS)

    mock_exec.execute.assert_not_called()
    assert info.get("skip") == "no_tool_call"
    assert reward == pytest.approx(-0.1)
    assert info["engagement_done"] is False
    assert env.active_host == "172.18.0.5"  # engagement continues


def test_skip_still_counts_against_safety_cap():
    with patch("rl.environment.LLMClient") as mock_llm_cls, \
         patch("rl.environment.Executor") as mock_exec_cls:
        mock_llm = mock_llm_cls.return_value
        mock_exec = mock_exec_cls.return_value
        mock_exec.execute.return_value = _res("Host: 172.18.0.250 (fixture_host)\tStatus: Up\n")
        mock_llm.complete.return_value = _req("nmap -sn 172.18.0.0/24 -oG -")
        env = Environment(EnvironmentConfig(container_name="test", max_steps=40, max_engagement_steps=2))
        env.reset()
        env._state.update("172.18.0.5", {"is_alive": True})
        env.start_engagement("172.18.0.5")
        mock_llm.complete.side_effect = ValueError("no tool call")

        env.interact(Action.SCAN_PORTS)
        _, _, _, info2 = env.interact(Action.SCAN_PORTS)

        assert info2["engagement_done"] is True, "repeated skips must not let an engagement run forever"


# ── interact(): episode-level done ──────────────────────────────────────────────

def test_done_triggers_at_max_steps(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True})
    env.start_engagement("172.18.0.5")
    mock_llm.complete.return_value = _req("nmap -sV -p 22 172.18.0.5 -oG -")
    mock_exec.execute.return_value = _res("")

    # env_mocks: max_steps=5, max_engagement_steps=3 — re-engage after the first
    # engagement's cap to reach the episode budget without an exploit.
    for _ in range(3):
        env.interact(Action.SCAN_PORTS)
    env.start_engagement("172.18.0.5")
    _, _, done, _ = env.interact(Action.SCAN_PORTS)
    assert not done
    _, _, done, _ = env.interact(Action.SCAN_PORTS)
    assert done


def test_step_count_increments(env_mocks):
    env, mock_llm, mock_exec = env_mocks
    env._state.update("172.18.0.5", {"is_alive": True})
    env.start_engagement("172.18.0.5")
    mock_llm.complete.return_value = _req("nmap -sV -p 22 172.18.0.5 -oG -")
    mock_exec.execute.return_value = _res("")

    assert env.step_count == 0
    env.interact(Action.SCAN_PORTS)
    assert env.step_count == 1
