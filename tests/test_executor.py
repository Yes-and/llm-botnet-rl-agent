from unittest.mock import MagicMock, patch

import pytest

from agent.executor import ALLOWED_BINARIES, CommandResult, Executor, format_tool_result


def make_proc(stdout="", stderr="", returncode=0):
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


def test_allowed_binary_reaches_subprocess():
    executor = Executor("test-container")
    with patch("agent.executor.subprocess.run", return_value=make_proc("scan output")) as mock_run:
        result = executor.execute("nmap -sV target")
    mock_run.assert_called_once()
    assert result.exit_code == 0
    assert "scan output" in result.output


@pytest.mark.parametrize("binary", ALLOWED_BINARIES)
def test_all_allowed_binaries_pass(binary):
    executor = Executor("test-container")
    with patch("agent.executor.subprocess.run", return_value=make_proc()):
        result = executor.execute(f"{binary} --help")
    assert result.exit_code == 0


@pytest.mark.parametrize("command", [
    "bash -c 'id'",
    "sh -c 'whoami'",
    "rm -rf /",
    "/usr/bin/nmap -sV target",
    "./nmap -sV target",
    "sudo nmap -sV target",
    "env nmap -sV target",
])
def test_disallowed_binary_rejected(command):
    executor = Executor("test-container")
    result = executor.execute(command)
    assert result.exit_code == -1
    assert "[REJECTED]" in result.output


def test_empty_command_rejected():
    executor = Executor("test-container")
    result = executor.execute("")
    assert result.exit_code == -1


# ---------------------------------------------------------------------------
# Blocklist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", [
    "nmap -sV target; rm -rf /",
    "nmap -sV target && rm /etc/passwd",
    "python3 -c 'import os; os.system(\"rm foo\")'",
])
def test_rm_pattern_rejected(command):
    executor = Executor("test-container")
    result = executor.execute(command)
    assert result.exit_code == -1
    assert "[REJECTED]" in result.output


@pytest.mark.parametrize("command", [
    "nmap -oX - target | dd of=/dev/sda",
    "python3 -c 'import subprocess; subprocess.run([\"dd\", \"if=/dev/zero\"])'",
])
def test_dd_pattern_rejected(command):
    executor = Executor("test-container")
    result = executor.execute(command)
    assert result.exit_code == -1


def test_mkfs_pattern_rejected():
    executor = Executor("test-container")
    result = executor.execute("nmap -sV t && mkfs.ext4 /dev/sda")
    assert result.exit_code == -1


def test_fork_bomb_rejected():
    executor = Executor("test-container")
    result = executor.execute("nmap -sV t && :(){ :|:& };:")
    assert result.exit_code == -1


@pytest.mark.parametrize("command", [
    "nmap -oX - target > /dev/sda",
    "python3 -c '' > /dev/mem",
])
def test_dev_write_rejected(command):
    executor = Executor("test-container")
    result = executor.execute(command)
    assert result.exit_code == -1


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_does_not_call_subprocess():
    executor = Executor("test-container", dry_run=True)
    with patch("agent.executor.subprocess.run") as mock_run:
        result = executor.execute("nmap -sV target")
    mock_run.assert_not_called()
    assert result.dry_run is True
    assert "[DRY RUN]" in result.output
    assert result.exit_code == 0


def test_dry_run_still_enforces_allowlist():
    executor = Executor("test-container", dry_run=True)
    result = executor.execute("bash -c 'id'")
    assert result.exit_code == -1


def test_dry_run_still_enforces_blocklist():
    executor = Executor("test-container", dry_run=True)
    result = executor.execute("nmap -sV t; rm -rf /")
    assert result.exit_code == -1


# ---------------------------------------------------------------------------
# Output handling
# ---------------------------------------------------------------------------


def test_truncation_when_output_exceeds_limit():
    executor = Executor("test-container", max_output_chars=100)
    with patch("agent.executor.subprocess.run", return_value=make_proc("A" * 200)):
        result = executor.execute("nmap -sV target")
    assert result.truncated is True
    assert "[... output truncated ...]" in result.output
    # start and end preserved, middle dropped — total length is bounded
    assert len(result.output) < 200


def test_no_truncation_when_output_within_limit():
    executor = Executor("test-container", max_output_chars=1000)
    with patch("agent.executor.subprocess.run", return_value=make_proc("short output")):
        result = executor.execute("nmap -sV target")
    assert result.truncated is False
    assert result.output == "short output"


def test_ansi_codes_stripped():
    executor = Executor("test-container")
    ansi_output = "\x1b[32mGreen text\x1b[0m and \x1b[1mbold\x1b[0m"
    with patch("agent.executor.subprocess.run", return_value=make_proc(ansi_output)):
        result = executor.execute("nmap -sV target")
    assert "\x1b" not in result.output
    assert "Green text" in result.output
    assert "bold" in result.output


def test_timeout_exit_code_annotated():
    executor = Executor("test-container", timeout=30)
    with patch("agent.executor.subprocess.run", return_value=make_proc(returncode=124)):
        result = executor.execute("nmap -sV target")
    assert result.exit_code == 124
    assert "[TIMEOUT]" in result.output


# ---------------------------------------------------------------------------
# subprocess call safety
# ---------------------------------------------------------------------------


def test_subprocess_called_with_list_not_shell():
    executor = Executor("my-container")
    with patch("agent.executor.subprocess.run", return_value=make_proc()) as mock_run:
        executor.execute("nmap -sV target")
    args, kwargs = mock_run.call_args
    assert isinstance(args[0], list), "must call subprocess with a list, not a shell string"
    assert kwargs.get("shell", False) is False


def test_subprocess_uses_in_container_timeout():
    executor = Executor("my-container", timeout=45)
    with patch("agent.executor.subprocess.run", return_value=make_proc()) as mock_run:
        executor.execute("nmap -sV target")
    cmd = mock_run.call_args[0][0]
    assert "timeout" in cmd
    assert "45" in cmd
    assert cmd.index("timeout") < cmd.index("/bin/bash")


def test_container_name_in_exec_command():
    executor = Executor("attacker-1")
    with patch("agent.executor.subprocess.run", return_value=make_proc()) as mock_run:
        executor.execute("nmap -sV target")
    cmd = mock_run.call_args[0][0]
    assert "attacker-1" in cmd


# ---------------------------------------------------------------------------
# format_tool_result
# ---------------------------------------------------------------------------


def test_format_tool_result_includes_exit_code_and_output():
    result = CommandResult(
        command="nmap -sV target",
        output="22/tcp open ssh",
        exit_code=0,
        truncated=False,
        dry_run=False,
    )
    formatted = format_tool_result(result)
    assert "exit_code: 0" in formatted
    assert "22/tcp open ssh" in formatted
