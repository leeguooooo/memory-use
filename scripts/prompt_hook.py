#!/usr/bin/env python3
"""Bound the Claude Code prompt hook without extending its five-second host timeout."""
import os
from pathlib import Path
import subprocess
import sys


PROMPT_TIMEOUT = 3.0


def run_prompt_hook(command, payload, timeout=PROMPT_TIMEOUT):
    """Publish only complete successful output; run() kills and reaps timed-out workers."""
    try:
        result = subprocess.run(command, input=payload, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        return result.stdout if result.returncode == 0 else b""
    except (OSError, subprocess.TimeoutExpired):
        return b""


def main():
    # Skip the release check even when called outside the packaged hook command.
    os.environ["MEMORY_USE_NO_UPDATE_CHECK"] = "1"
    command = [sys.executable, str(Path(__file__).with_name("memory_use.py")), "hook", "prompt"]
    sys.stdout.buffer.write(run_prompt_hook(command, sys.stdin.buffer.read()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
