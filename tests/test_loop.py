from dataclasses import dataclass
from unittest.mock import MagicMock, call, patch

from agent.executor import CommandResult
from agent.loop import EpisodeConfig, EpisodeResult, StepRecord, run_episode
from agent.llm_client import CommandRequest


def make_request(step: int) -> CommandRequest:
    return CommandRequest(
        command=f"nmap -sV target-{step}",
        tool_call_id=f"call_{step}",
        assistant_message={"role": "assistant", "tool_calls": [{"id": f"call_{step}"}]},
    )


def make_result(step: int) -> CommandResult:
    return CommandResult(
        command=f"nmap -sV target-{step}",
        output=f"output-{step}",
        exit_code=0,
        truncated=False,
        dry_run=False,
    )


# ---------------------------------------------------------------------------
# Episode structure
# ---------------------------------------------------------------------------


def test_run_episode_returns_episode_result():
    config = EpisodeConfig(task="test task", container_name="c", max_steps=2)
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.side_effect = [make_request(i) for i in range(2)]
        MockExecutor.return_value.execute.side_effect = [make_result(i) for i in range(2)]
        result = run_episode(config)
    assert isinstance(result, EpisodeResult)


def test_run_episode_runs_for_max_steps():
    config = EpisodeConfig(task="test task", container_name="c", max_steps=5)
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.side_effect = [make_request(i) for i in range(5)]
        MockExecutor.return_value.execute.side_effect = [make_result(i) for i in range(5)]
        result = run_episode(config)
    assert len(result.steps) == 5


def test_malformed_tool_call_error_does_not_end_episode():
    config = EpisodeConfig(task="test task", container_name="c", max_steps=2)
    malformed_request = CommandRequest(
        command="", tool_call_id="call_0", assistant_message={"role": "assistant", "tool_calls": [{"id": "call_0"}]},
        error="Malformed tool call: expected a JSON object",
    )
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.side_effect = [malformed_request, make_request(1)]
        MockExecutor.return_value.execute.side_effect = [make_result(1)]
        result = run_episode(config)
    assert len(result.steps) == 2
    assert result.stop_reason is None
    assert result.steps[0].result.exit_code == -1
    MockExecutor.return_value.execute.assert_called_once()  # never called for the malformed step


def test_malformed_tool_call_does_not_echo_raw_content_to_model():
    # Guards against feeding potentially garbled/leaked-token content from a
    # malformed tool call back into the model's own conversation history.
    config = EpisodeConfig(task="test task", container_name="c", max_steps=1)
    raw_garbled = "<|tool_calls_section_begin|> some leaked token garbage functions.execute_command:7"
    malformed_request = CommandRequest(
        command="", tool_call_id="call_0", assistant_message={"role": "assistant", "tool_calls": [{"id": "call_0"}]},
        error=raw_garbled,
    )
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.return_value = malformed_request
        result = run_episode(config)
    tool_message = result.steps[0]
    assert raw_garbled not in tool_message.result.output
    # the raw detail must still be reachable via the request, for our own logging
    assert tool_message.request.error == raw_garbled


def test_value_error_sets_stop_reason_and_ends_episode():
    config = EpisodeConfig(task="test task", container_name="c", max_steps=5)
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.side_effect = [
            make_request(0),
            ValueError("Model returned no tool call. Text response: 'here is my plan'"),
        ]
        MockExecutor.return_value.execute.side_effect = [make_result(0)]
        result = run_episode(config)
    assert len(result.steps) == 1
    assert result.stop_reason == "Model returned no tool call. Text response: 'here is my plan'"


def test_stop_reason_is_none_on_normal_completion():
    config = EpisodeConfig(task="test task", container_name="c", max_steps=2)
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.side_effect = [make_request(i) for i in range(2)]
        MockExecutor.return_value.execute.side_effect = [make_result(i) for i in range(2)]
        result = run_episode(config)
    assert result.stop_reason is None


def test_on_step_truthy_return_stops_episode_early():
    config = EpisodeConfig(task="test task", container_name="c", max_steps=5)
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.side_effect = [make_request(i) for i in range(5)]
        MockExecutor.return_value.execute.side_effect = [make_result(i) for i in range(5)]
        result = run_episode(config, on_step=lambda record: record.step == 1)
    assert len(result.steps) == 2


def test_step_records_have_correct_indices():
    config = EpisodeConfig(task="test task", container_name="c", max_steps=3)
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.side_effect = [make_request(i) for i in range(3)]
        MockExecutor.return_value.execute.side_effect = [make_result(i) for i in range(3)]
        result = run_episode(config)
    assert [r.step for r in result.steps] == [0, 1, 2]


def test_step_records_link_request_and_result():
    config = EpisodeConfig(task="test task", container_name="c", max_steps=1)
    req = make_request(0)
    res = make_result(0)
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.return_value = req
        MockExecutor.return_value.execute.return_value = res
        result = run_episode(config)
    assert result.steps[0].request is req
    assert result.steps[0].result is res


def test_episode_result_carries_task():
    config = EpisodeConfig(task="find the target", container_name="c", max_steps=1)
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.return_value = make_request(0)
        MockExecutor.return_value.execute.return_value = make_result(0)
        result = run_episode(config)
    assert result.task == "find the target"


# ---------------------------------------------------------------------------
# Message history
# ---------------------------------------------------------------------------


def test_executor_receives_command_from_llm():
    config = EpisodeConfig(task="test task", container_name="c", max_steps=1)
    req = make_request(0)
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.return_value = req
        MockExecutor.return_value.execute.return_value = make_result(0)
        run_episode(config)
    MockExecutor.return_value.execute.assert_called_once_with(req.command)


def test_initial_messages_contain_system_and_user():
    config = EpisodeConfig(task="my task", container_name="c", max_steps=1)
    captured_messages = []

    def capture_complete(messages):
        captured_messages.append(list(messages))
        return make_request(0)

    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.side_effect = capture_complete
        MockExecutor.return_value.execute.return_value = make_result(0)
        run_episode(config)

    first_call_messages = captured_messages[0]
    roles = [m["role"] for m in first_call_messages]
    assert roles[0] == "system"
    assert roles[1] == "user"
    assert "my task" in first_call_messages[1]["content"]


def test_messages_grow_by_two_per_step():
    config = EpisodeConfig(task="test task", container_name="c", max_steps=3)
    message_lengths = []

    def capture_complete(messages):
        message_lengths.append(len(messages))
        return make_request(len(message_lengths) - 1)

    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.side_effect = capture_complete
        MockExecutor.return_value.execute.side_effect = [make_result(i) for i in range(3)]
        run_episode(config)

    # starts at 2 (system + user), grows by 2 each step (assistant + tool)
    assert message_lengths == [2, 4, 6]


def test_tool_result_message_uses_correct_tool_call_id():
    config = EpisodeConfig(task="test task", container_name="c", max_steps=2)
    req = make_request(0)
    appended_messages = []

    def capture_complete(messages):
        appended_messages.extend(messages[len(appended_messages):])
        return req

    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.side_effect = capture_complete
        MockExecutor.return_value.execute.return_value = make_result(0)
        run_episode(config)

    tool_messages = [m for m in appended_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == req.tool_call_id


# ---------------------------------------------------------------------------
# Config passthrough
# ---------------------------------------------------------------------------


def test_llm_client_receives_model_from_config():
    config = EpisodeConfig(task="t", container_name="c", max_steps=1, model="some-model")
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.return_value = make_request(0)
        MockExecutor.return_value.execute.return_value = make_result(0)
        run_episode(config)
    _, kwargs = MockClient.call_args
    assert kwargs["model"] == "some-model"


def test_llm_client_receives_base_url_and_api_key_env_from_config():
    config = EpisodeConfig(
        task="t", container_name="c", max_steps=1,
        base_url="https://openrouter.ai/api/v1", api_key_env="OPENROUTER_API_KEY",
    )
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.return_value = make_request(0)
        MockExecutor.return_value.execute.return_value = make_result(0)
        run_episode(config)
    _, kwargs = MockClient.call_args
    assert kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert kwargs["api_key_env"] == "OPENROUTER_API_KEY"


def test_executor_receives_container_and_dry_run_from_config():
    config = EpisodeConfig(task="t", container_name="my-container", max_steps=1, dry_run=True)
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.return_value = make_request(0)
        MockExecutor.return_value.execute.return_value = make_result(0)
        run_episode(config)
    args, kwargs = MockExecutor.call_args
    assert args[0] == "my-container"
    assert kwargs["dry_run"] is True


def test_executor_receives_timeout_and_output_limit_from_config():
    config = EpisodeConfig(task="t", container_name="c", max_steps=1, timeout=30, max_output_chars=1000)
    with patch("agent.loop.LLMClient") as MockClient, \
         patch("agent.loop.Executor") as MockExecutor:
        MockClient.return_value.complete.return_value = make_request(0)
        MockExecutor.return_value.execute.return_value = make_result(0)
        run_episode(config)
    _, kwargs = MockExecutor.call_args
    assert kwargs["timeout"] == 30
    assert kwargs["max_output_chars"] == 1000
