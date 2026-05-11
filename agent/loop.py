from dataclasses import dataclass, field

from agent.executor import CommandResult, Executor
from agent.llm_client import CommandRequest, LLMClient


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
    raise NotImplementedError
