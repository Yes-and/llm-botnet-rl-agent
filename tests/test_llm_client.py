from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.llm_client import LLMClient


def make_response(tool_call_args, content=""):
    tool_calls = [
        SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name="execute_command", arguments=args))
        for i, args in enumerate(tool_call_args)
    ]
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make_client():
    with patch("agent.llm_client.openai.OpenAI"), patch.dict("os.environ", {"DEEPINFRA_API_KEY": "test"}):
        client = LLMClient()
    client._client.chat.completions.create = MagicMock()
    return client


def test_valid_args_returns_command_with_no_error():
    client = make_client()
    client._client.chat.completions.create.return_value = make_response(['{"command": "ls"}'])
    request = client.complete([])
    assert request.command == "ls"
    assert request.error is None


def test_args_as_list_instead_of_object_is_recoverable():
    # real case: model wrapped its args in a JSON array instead of a single object
    client = make_client()
    client._client.chat.completions.create.return_value = make_response(['[{"command": "ls"}, {}]'])
    request = client.complete([])
    assert request.error is not None
    assert request.command == ""
    # assistant_message must still carry the real tool_call id so the conversation stays valid
    assert request.assistant_message["tool_calls"][0]["id"] == "call_0"


def test_args_missing_command_key_is_recoverable():
    client = make_client()
    client._client.chat.completions.create.return_value = make_response(['{"foo": "bar"}'])
    request = client.complete([])
    assert request.error is not None


def test_non_string_command_value_is_recoverable():
    client = make_client()
    client._client.chat.completions.create.return_value = make_response(['{"command": ["ls", "-la"]}'])
    request = client.complete([])
    assert request.error is not None
    assert request.command == ""


def test_invalid_json_args_is_recoverable():
    client = make_client()
    client._client.chat.completions.create.return_value = make_response(["not json at all"])
    request = client.complete([])
    assert request.error is not None


def test_no_tool_call_at_all_still_raises():
    client = make_client()
    client._client.chat.completions.create.return_value = make_response([], content="here is my plan")
    with pytest.raises(ValueError):
        client.complete([])
