from __future__ import annotations

import shlex
import subprocess
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .commands import require_binary
from .names import slugify


def _quote(value: str) -> str:
    return shlex.quote(value)


_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x1b]*(?:\x07|\x1b\\)|\\)")


def _strip_ansi(value: str) -> str:
    return _ANSI_RE.sub("", value)


def _trim_trailing_prompt(scrollback: str) -> str:
    lines = scrollback.splitlines()
    while lines and not _strip_ansi(lines[-1]).strip():
        lines.pop()
    while lines:
        plain = _strip_ansi(lines[-1]).strip()
        if plain.startswith("╰─") or plain.startswith("╭─"):
            lines.pop()
            continue
        break
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def _history_commands(scrollback: str) -> list[str]:
    commands: list[str] = []
    for line in _strip_ansi(scrollback).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^❯\s+(.+)$", stripped)
        if not match:
            match = re.match(r"^.+\s[$#]\s+(.+)$", stripped)
        if not match:
            continue
        command = match.group(1).strip()
        if command and command not in commands[-3:]:
            commands.append(command)
    return commands


def _restore_details(snapshot: dict[str, Any]) -> tuple[str, str]:
    name = str(snapshot.get("name") or snapshot.get("os_window", {}).get("title") or "workspace")
    raw_timestamp = str(snapshot.get("updated_at") or snapshot.get("created_at") or "")
    try:
        if "-" in raw_timestamp:
            restored_at = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00")).astimezone()
        else:
            restored_at = datetime.strptime(raw_timestamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone()
        formatted = restored_at.strftime("%I:%M %p on %d/%m/%Y")
    except ValueError:
        formatted = raw_timestamp or "unknown time"
    return name, formatted


def _restore_message(snapshot: dict[str, Any]) -> str:
    name, formatted = _restore_details(snapshot)
    return f"Restored {name} from {formatted}"


def _prepare_restore_files(snapshot: dict[str, Any], snapshot_root: Path) -> None:
    restore_dir = snapshot_root / "restore"
    history_dir = snapshot_root / "history"
    restore_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)

    restore_name, restore_time = _restore_details(snapshot)
    epoch = int(time.time())

    for tab in snapshot.get("os_window", {}).get("tabs") or []:
        for window in tab.get("windows") or []:
            scrollback_file = window.get("scrollback_file")
            if not scrollback_file:
                continue
            source = snapshot_root / str(scrollback_file)
            if not source.exists():
                continue
            raw = source.read_text(encoding="utf-8", errors="replace")
            tab_index = int(tab.get("index") or 0) + 1
            window_index = int(window.get("index") or 0) + 1
            restore_rel = f"restore/tab-{tab_index:03d}-window-{window_index:03d}.txt"
            history_rel = f"history/tab-{tab_index:03d}-window-{window_index:03d}.zsh_history"
            restore_path = snapshot_root / restore_rel
            history_path = snapshot_root / history_rel
            restore_path.write_text(_trim_trailing_prompt(raw), encoding="utf-8")
            history_path.write_text(
                "".join(f": {epoch}:0;{command}\n" for command in _history_commands(raw)),
                encoding="utf-8",
            )
            window["restore_file"] = restore_rel
            window["history_file"] = history_rel
            window["restore_name"] = restore_name
            window["restore_time"] = restore_time


def _launch_line(
    window: dict[str, Any],
    *,
    should_refocus: bool,
    workspace_name: str,
    workspace_kind: str,
    snapshot_root: Path | None = None,
    tab_title: str | None = None,
) -> str:
    parts = ["launch"]
    title = str(window.get("title") or "")
    cwd = window.get("cwd")
    parts.append(f"--env=KTR_WORKSPACE_NAME={_quote(workspace_name)}")
    parts.append(f"--env=KTR_WORKSPACE_KIND={_quote(workspace_kind)}")
    parts.append(f"--env=KTR_WORKSPACE_SLUG={_quote(slugify(workspace_name))}")
    parts.append(f"--var=ktr_workspace_name={_quote(workspace_name)}")
    parts.append(f"--var=ktr_workspace_kind={_quote(workspace_kind)}")
    parts.append(f"--var=ktr_workspace_slug={_quote(slugify(workspace_name))}")
    if tab_title:
        parts.append(f"--tab-title={_quote(tab_title)}")
    if title:
        parts.append(f"--title={_quote(title)}")
    if cwd:
        parts.append(f"--cwd={_quote(str(cwd))}")
    if should_refocus:
        parts.append("--var=ktr_focus=1")
    history_file = window.get("history_file")
    if history_file and snapshot_root:
        parts.append(f"--env=HISTFILE={_quote(str(snapshot_root / str(history_file)))}")
    restore_file = window.get("restore_file") or window.get("scrollback_file")
    if restore_file and snapshot_root:
        scrollback_path = snapshot_root / str(restore_file)
        restore_name = str(window.get("restore_name") or "workspace")
        restore_time = str(window.get("restore_time") or "unknown time")
        command = (
            "if [ -r \"$1\" ]; then cat \"$1\"; printf '\\n'; fi; "
            "cols=$(tput cols 2>/dev/null || printf 80); "
            "case \"$cols\" in ''|*[!0-9]*) cols=80;; esac; "
            "[ \"$cols\" -gt 1 ] && cols=$((cols - 1)); "
            "printf '\\033[38;5;242m'; i=0; "
            "while [ \"$i\" -lt \"$cols\" ]; do printf '─'; i=$((i + 1)); done; "
            "printf '\\033[0m\\n\\033[37mRestored \\033[1m%s\\033[22m from %s\\033[0m\\n\\n' \"$2\" \"$3\"; "
            "exec \"${SHELL:-/usr/bin/zsh}\" -l"
        )
        parts.extend(
            [
                "sh",
                "-c",
                _quote(command),
                "ktr-restore-scrollback",
                _quote(str(scrollback_path)),
                _quote(restore_name),
                _quote(restore_time),
            ]
        )
    return " ".join(parts)


def render_session(snapshot: dict[str, Any], *, snapshot_root: Path | None = None) -> str:
    lines = [
        "# Generated by kitty-tabs-recover.",
        "# Scrollback text is saved beside snapshot.json; live processes are not resurrected.",
    ]

    os_title = snapshot.get("os_window", {}).get("title")
    workspace_name = str(snapshot.get("name") or os_title or "workspace")
    workspace_kind = str(snapshot.get("kind") or "named")
    if snapshot_root:
        _prepare_restore_files(snapshot, snapshot_root)
    if os_title:
        lines.append(f"os_window_title {_quote(str(os_title))}")

    for tab_index, tab in enumerate(snapshot.get("os_window", {}).get("tabs") or []):
        title = str(tab.get("title") or f"Tab {tab_index + 1}")
        lines.append(f"new_tab {_quote(title)}")

        layout = tab.get("layout")
        if layout:
            lines.append(f"layout {_quote(str(layout))}")

        windows = tab.get("windows") or []
        if not windows:
            lines.append("launch")
            continue

        for window_index, window in enumerate(windows):
            lines.append(
                _launch_line(
                    window,
                    should_refocus=bool(window.get("is_active")),
                    workspace_name=workspace_name,
                    workspace_kind=workspace_kind,
                    snapshot_root=snapshot_root,
                    tab_title=title if tab_index == 0 and window_index == 0 else None,
                )
            )
        if any(window.get("is_active") for window in windows):
            lines.append("focus_matching_window var:ktr_focus=1")

    active_tab_index = 0
    for tab in snapshot.get("os_window", {}).get("tabs") or []:
        if tab.get("is_active"):
            active_tab_index = int(tab.get("index") or 0)
            break
    if active_tab_index:
        lines.append(f"focus_tab {active_tab_index}")

    return "\n".join(lines) + "\n"


def write_session(snapshot_path: Path) -> Path:
    import json

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    session_path = snapshot_path.parent / "session.kitty"
    session_path.write_text(render_session(snapshot, snapshot_root=snapshot_path.parent), encoding="utf-8")
    return session_path


def open_session(session_path: Path) -> None:
    require_binary("kitty")
    subprocess.Popen(["kitty", "--session", str(session_path)], start_new_session=True)
