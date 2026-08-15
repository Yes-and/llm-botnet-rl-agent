from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.llm_client import LLMClient


def make_response(tool_call_args, content="", refusal=None, tool_name="execute_command"):
    tool_calls = [
        SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=tool_name, arguments=args))
        for i, args in enumerate(tool_call_args)
    ]
    message = SimpleNamespace(content=content, tool_calls=tool_calls, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make_client(declare_futile=False):
    with patch("agent.llm_client.openai.OpenAI"), patch.dict("os.environ", {"DEEPINFRA_API_KEY": "test"}):
        client = LLMClient(declare_futile=declare_futile)
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


def test_no_tool_call_surfaces_refusal_when_present():
    # real case (Opus 5, 2026-07-28): content=None gave zero signal about why — the
    # actual reason was only visible in .refusal, which the old error never read.
    client = make_client()
    client._client.chat.completions.create.return_value = make_response(
        [], content=None, refusal="This request triggered restrictions on violative cyber content."
    )
    with pytest.raises(ValueError, match="violative cyber content"):
        client.complete([])


def test_no_tool_call_without_refusal_omits_it_from_message():
    # the common case (no refusal field at all, or None) — error should stay clean,
    # not print "Refusal: None" noise.
    client = make_client()
    client._client.chat.completions.create.return_value = make_response([], content=None)
    with pytest.raises(ValueError) as exc_info:
        client.complete([])
    assert "Refusal" not in str(exc_info.value)


def test_empty_choices_raises_after_exhausting_retries():
    # real case: OpenRouter provider-fallback routing returned `choices: null` on every
    # attempt (the underlying "tool_call_ids did not have response messages" 400, disguised
    # as a 200-shaped response so the SDK's own retry logic never sees it as retryable)
    client = make_client()
    client._client.chat.completions.create.return_value = SimpleNamespace(choices=None)
    with patch("agent.llm_client.time.sleep") as mock_sleep:
        with pytest.raises(ValueError):
            client.complete([])
    assert client._client.chat.completions.create.call_count == client._no_choices_retries + 1
    assert mock_sleep.call_count == client._no_choices_retries


def test_declare_futile_disabled_by_default_not_sent_to_api():
    # Reproducibility guarantee: every existing config's tool list/token cost must
    # stay byte-for-byte identical unless it opts in.
    client = make_client()
    assert [t["function"]["name"] for t in client._tools] == ["execute_command"]


def test_declare_futile_tool_added_when_enabled():
    client = make_client(declare_futile=True)
    assert "declare_futile" in [t["function"]["name"] for t in client._tools]


def test_declare_futile_call_parsed_with_reason():
    client = make_client(declare_futile=True)
    client._client.chat.completions.create.return_value = make_response(
        ['{"reason": "credentials exhausted"}'], tool_name="declare_futile",
    )
    request = client.complete([])
    assert request.tool_name == "declare_futile"
    assert request.command == "credentials exhausted"
    assert request.error is None


def test_declare_futile_call_missing_reason_is_recoverable():
    client = make_client(declare_futile=True)
    client._client.chat.completions.create.return_value = make_response(
        ["{}"], tool_name="declare_futile",
    )
    request = client.complete([])
    assert request.tool_name == "declare_futile"
    assert request.command == "(no reason given)"


def test_empty_choices_recovers_on_retry():
    # the same failure mode as above, but resolves on a later attempt — confirms the
    # manual retry loop actually retries the whole request, not just re-reads a cached one
    client = make_client()
    client._client.chat.completions.create.side_effect = [
        SimpleNamespace(choices=None),
        make_response(['{"command": "ls"}']),
    ]
    with patch("agent.llm_client.time.sleep"):
        request = client.complete([])
    assert request.command == "ls"
    assert client._client.chat.completions.create.call_count == 2
