from dataclasses import dataclass, field

from agent.executor import CommandResult, Executor, format_tool_result
from agent.llm_client import CommandRequest, LLMClient, build_initial_messages


@dataclass
class EpisodeConfig:
    task: str
    container_name: str
    max_steps: int = 10
    dry_run: bool = False
    timeout: int = 60
    max_output_chars: int = 4000
    model: str = "moonshotai/Kimi-K2.6"


@dataclass
class StepRecord:
    step: int
    request: CommandRequest
    result: CommandResult


@dataclass
class EpisodeResult:
    task: str
    steps: list[StepRecord] = field(default_factory=list)


def run_episode(config: EpisodeConfig) -> EpisodeResult:
    client = LLMClient(model=config.model)
    executor = Executor(
        config.container_name,
        dry_run=config.dry_run,
        timeout=config.timeout,
        max_output_chars=config.max_output_chars,
    )

    messages = build_initial_messages(config.task)
    episode = EpisodeResult(task=config.task)

    for step in range(config.max_steps):
        try:
            request = client.complete(messages)
        except ValueError:
            break
        messages.append(request.assistant_message)

        result = executor.execute(request.command)
        messages.append({
            "role": "tool",
            "tool_call_id": request.tool_call_id,
            "content": format_tool_result(result),
        })

        episode.steps.append(StepRecord(step=step, request=request, result=result))

    return episode
