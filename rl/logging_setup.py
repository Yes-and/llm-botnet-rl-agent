"""
Logging configuration for training and evaluation runs.

Console (stdout): INFO from rl.* only — one clean line per step, no noise.
File:             DEBUG from all loggers — full audit trail (LLM commands, exit
                  codes, executor rejections, warnings).

Call setup_logging() once at the entry point (scripts/train.py, etc.) before
any Environment is constructed.
"""

import logging
import sys


class _RLFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("rl.")


def setup_logging(log_file: str = "run.log") -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.addFilter(_RLFilter())
    console.setFormatter(logging.Formatter("%(message)s"))

    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s"))

    root.addHandler(console)
    root.addHandler(fh)
