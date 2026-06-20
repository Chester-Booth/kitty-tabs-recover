from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .commands import CommandError, require_binary, run, run_json


def _socket_candidates() -> list[str]:
    target = os.environ.get("KITTY_LISTEN_ON")
    if target:
        return [target]

    candidates: list[str] = []
    for path in sorted(Path("/tmp").glob("kitty-tabs-recover-*")):
        candidates.append(f"unix:{path}")
    return candidates


def target_for_pid(pid: int | str | None) -> str | None:
    if not pid:
        return None
    path = Path(f"/tmp/kitty-tabs-recover-{pid}")
    if path.exists():
        return f"unix:{path}"
    return None


def kitten_base(target: str | None = None) -> list[str]:
    require_binary("kitten")
    argv = ["kitten", "@"]
    if target:
        argv.extend(["--to", target])
    return argv


def ls(target: str | None = None) -> list[dict[str, Any]]:
    errors: list[str] = []
    candidates = [target] if target else _socket_candidates()
    if not candidates:
        candidates = [""]

    data = None
    for target in candidates:
        try:
            data = run_json([*kitten_base(target or None), "ls"])
            break
        except CommandError as exc:
            errors.append(str(exc))

    if data is None:
        detail = errors[-1] if errors else "no socket candidates found"
        raise CommandError(
            "Could not query kitty. Enable remote control and restart kitty. "
            "When running outside kitty, either set KITTY_LISTEN_ON or use listen_on "
            "unix:/tmp/kitty-tabs-recover. Original error: " + detail
        )

    if not isinstance(data, list):
        raise CommandError("Unexpected kitty ls response")
    return data


def get_scrollback(window_id: int, *, target: str | None = None) -> str:
    candidates = ([target] if target else _socket_candidates()) or [""]
    for target in candidates:
        result = run(
            [
                *kitten_base(target or None),
                "get-text",
                "--match",
                f"id:{window_id}",
                "--extent",
                "all",
                "--ansi",
            ],
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
    return ""


def close_os_window(os_window_id: int) -> None:
    candidates = _socket_candidates() or [""]
    for target in candidates:
        result = run(
            [*kitten_base(target or None), "close-window", "--match", f"state:focused_os_window"],
            check=False,
        )
        if result.returncode == 0:
            return
