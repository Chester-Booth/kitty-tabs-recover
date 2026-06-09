from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


class CommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise CommandError(f"Required command not found: {name}")
    return path


def run(argv: list[str], *, check: bool = True, input_text: str | None = None) -> CommandResult:
    try:
        completed = subprocess.run(
            argv,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise CommandError(str(exc)) from exc

    result = CommandResult(completed.stdout, completed.stderr, completed.returncode)
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise CommandError(f"{argv[0]} failed: {detail}")
    return result


def run_json(argv: list[str]) -> Any:
    result = run(argv)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CommandError(f"{argv[0]} returned invalid JSON") from exc
