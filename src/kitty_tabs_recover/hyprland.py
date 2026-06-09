from __future__ import annotations

from typing import Any

from .commands import CommandError, require_binary, run, run_json


def available() -> bool:
    try:
        require_binary("hyprctl")
    except CommandError:
        return False
    return True


def active_window() -> dict[str, Any] | None:
    if not available():
        return None
    try:
        data = run_json(["hyprctl", "activewindow", "-j"])
    except CommandError:
        return None
    return data if isinstance(data, dict) and data else None


def killactive() -> None:
    require_binary("hyprctl")
    run(["hyprctl", "dispatch", "killactive"])


def is_kitty_window(window: dict[str, Any] | None) -> bool:
    if not window:
        return False
    values = [
        str(window.get("class", "")),
        str(window.get("initialClass", "")),
        str(window.get("title", "")),
    ]
    return any("kitty" in value.lower() for value in values)
