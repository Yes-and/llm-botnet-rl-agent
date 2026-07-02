"""
Logging configuration for training and evaluation runs.

Console (stdout):  INFO from rl.* only — one clean line per step, no noise.
train.log:         INFO from rl.* and agent.* — step lines, episode summaries,
                   warnings, errors. Small; always written.
train.debug.log:   DEBUG from all loggers — full audit trail including raw LLM
                   request/response payloads. Large; open only when debugging.

Call setup_logging() once at the entry point (scripts/train.py, etc.) before
any Environment is constructed.
"""

import logging
import sys


class _RLFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("rl.")


class _ProjectFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("rl.") or record.name.startswith("agent.")


def setup_logging(log_file: str = "run.log") -> None:
    debug_log_file = log_file.replace(".log", ".debug.log")
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.addFilter(_RLFilter())
    console.setFormatter(logging.Formatter("%(message)s"))

    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.INFO)
    fh.addFilter(_ProjectFilter())
    fh.setFormatter(fmt)

    fh_debug = logging.FileHandler(debug_log_file)
    fh_debug.setLevel(logging.DEBUG)
    fh_debug.setFormatter(fmt)

    root.addHandler(console)
    root.addHandler(fh)
    root.addHandler(fh_debug)
