from __future__ import annotations

import copy
import fcntl
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import kitty
from .commands import CommandError
from .names import slugify, timestamp
from .paths import autosaves_dir, data_dir, ensure_dirs, workspaces_dir


@dataclass(frozen=True)
class StoredSnapshot:
    name: str
    kind: str
    path: Path
    data: dict[str, Any]


def tab_count(os_window: dict[str, Any]) -> int:
    return len(os_window.get("tabs") or [])


def window_title(os_window: dict[str, Any]) -> str:
    tabs = os_window.get("tabs") or []
    active = next((tab for tab in tabs if tab.get("is_active")), None) or (tabs[0] if tabs else {})
    windows = active.get("windows") or []
    active_window = next((window for window in windows if window.get("is_active")), None) or (windows[0] if windows else {})
    return str(active_window.get("title") or active.get("title") or f"kitty-{os_window.get('id', 'window')}")


def choose_focused_os_window(os_windows: list[dict[str, Any]], hypr_window: dict[str, Any] | None = None) -> dict[str, Any] | None:
    focused = [
        os_window
        for os_window in os_windows
        if any(tab.get("is_focused") or tab.get("is_active") and tab.get("state") == "focused" for tab in os_window.get("tabs") or [])
    ]
    if len(focused) == 1:
        return focused[0]

    if hypr_window:
        title = str(hypr_window.get("title") or "")
        if title:
            for os_window in os_windows:
                if title == window_title(os_window):
                    return os_window
                for tab in os_window.get("tabs") or []:
                    if title == str(tab.get("title") or ""):
                        return os_window
                    for window in tab.get("windows") or []:
                        if title == str(window.get("title") or ""):
                            return os_window

    if len(os_windows) == 1:
        return os_windows[0]
    return None


def build_snapshot(
    os_window: dict[str, Any],
    *,
    name: str,
    kind: str,
    capture_scrollback: bool = True,
    kitty_target: str | None = None,
) -> dict[str, Any]:
    tabs: list[dict[str, Any]] = []
    for tab_index, tab in enumerate(os_window.get("tabs") or []):
        windows: list[dict[str, Any]] = []
        for window_index, window in enumerate(tab.get("windows") or []):
            window_id = int(window.get("id") or 0)
            scrollback = kitty.get_scrollback(window_id, target=kitty_target) if capture_scrollback and window_id else ""
            windows.append(
                {
                    "index": window_index,
                    "id": window_id,
                    "title": window.get("title") or "",
                    "cwd": window.get("cwd") or None,
                    "cmdline": window.get("cmdline") or [],
                    "env": window.get("env") or {},
                    "is_active": bool(window.get("is_active")),
                    "scrollback": scrollback,
                }
            )
        tabs.append(
            {
                "index": tab_index,
                "id": tab.get("id"),
                "title": tab.get("title") or "",
                "layout": tab.get("layout") or None,
                "is_active": bool(tab.get("is_active")),
                "windows": windows,
            }
        )

    return {
        "schema_version": 1,
        "name": name,
        "kind": kind,
        "created_at": timestamp(),
        "updated_at": timestamp(),
        "source": "kitten @ ls",
        "os_window": {
            "id": os_window.get("id"),
            "title": window_title(os_window),
            "tabs": tabs,
        },
    }


def _snapshot_dir(name: str, kind: str) -> Path:
    root = autosaves_dir() if kind == "autosave" else workspaces_dir()
    return root / slugify(name)


@contextmanager
def _storage_lock():
    ensure_dirs()
    path = data_dir() / ".lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(text)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def _write_snapshot_unlocked(snapshot: dict[str, Any]) -> Path:
    name = str(snapshot["name"])
    kind = str(snapshot.get("kind") or "named")
    target = _snapshot_dir(name, kind)
    target.mkdir(parents=True, exist_ok=True)

    for tab in snapshot["os_window"]["tabs"]:
        for window in tab["windows"]:
            scrollback = window.pop("scrollback", "")
            if scrollback:
                rel = f"scrollback/tab-{tab['index'] + 1:03d}-window-{window['index'] + 1:03d}.txt"
                scrollback_path = target / rel
                _atomic_write_text(scrollback_path, scrollback)
                window["scrollback_file"] = rel

    path = target / "snapshot.json"
    _atomic_write_text(path, json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    return path


def write_snapshot(snapshot: dict[str, Any]) -> Path:
    with _storage_lock():
        return _write_snapshot_unlocked(copy.deepcopy(snapshot))


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _autosave_identity(data: dict[str, Any]) -> tuple[Any, str]:
    os_window = data.get("os_window") or {}
    return os_window.get("id"), str(os_window.get("title") or "")


def mark_autosave_ephemeral(snapshot_path: Path, *, keep_days: int = 7) -> Path:
    """Keep only this autosave for its source window, and expire it later."""
    with _storage_lock():
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if data.get("kind") != "autosave":
            return snapshot_path

        expiry = datetime.now(timezone.utc) + timedelta(days=keep_days)
        data["expires_at"] = expiry.strftime("%Y%m%dT%H%M%SZ")
        _atomic_write_text(snapshot_path, json.dumps(data, indent=2, sort_keys=True) + "\n")

        identity = _autosave_identity(data)
        for item in load_snapshots(include_autosaves=True):
            if item.kind != "autosave" or item.path == snapshot_path:
                continue
            if _autosave_identity(item.data) == identity:
                _delete_snapshot_unlocked(item)
        return snapshot_path


def load_snapshots(*, include_autosaves: bool = True) -> list[StoredSnapshot]:
    ensure_dirs()
    roots: list[tuple[str, Path]] = [("named", workspaces_dir())]
    if include_autosaves:
        roots.append(("autosave", autosaves_dir()))

    snapshots: list[StoredSnapshot] = []
    for kind, root in roots:
        for path in sorted(root.glob("*/snapshot.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            snapshots.append(StoredSnapshot(str(data.get("name") or path.parent.name), kind, path, data))

    snapshots.sort(key=lambda item: str(item.data.get("updated_at") or ""), reverse=True)
    return snapshots


def current_workspace_key() -> tuple[str, str] | None:
    import os

    name = os.environ.get("KTR_WORKSPACE_NAME")
    kind = os.environ.get("KTR_WORKSPACE_KIND") or "named"
    if not name:
        return None
    return kind, name


def reload_snapshot(path: Path, kind: str) -> StoredSnapshot:
    data = json.loads(path.read_text(encoding="utf-8"))
    return StoredSnapshot(str(data.get("name") or path.parent.name), kind, path, data)


def rename_snapshot(item: StoredSnapshot, new_name: str) -> StoredSnapshot:
    with _storage_lock():
        new_name = new_name.strip()
        if not new_name:
            raise CommandError("Workspace name cannot be empty")

        new_kind = "named"
        target = _snapshot_dir(new_name, new_kind)
        if target.exists() and target.resolve() != item.path.parent.resolve():
            raise CommandError(f"Workspace already exists: {new_name}")

        source = item.path.parent
        data = dict(item.data)
        data["name"] = new_name
        data["kind"] = new_kind
        data["updated_at"] = timestamp()

        if source.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))

        path = target / "snapshot.json"
        _atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
        session_path = target / "session.kitty"
        if session_path.exists():
            session_path.unlink()
        return StoredSnapshot(new_name, new_kind, path, data)


def delete_snapshot(item: StoredSnapshot) -> None:
    with _storage_lock():
        _delete_snapshot_unlocked(item)


def _delete_snapshot_unlocked(item: StoredSnapshot) -> None:
    root = item.path.parent
    if not root.exists():
        return
    shutil.rmtree(root)


def prune_autosaves(keep: int, *, per_window: int = 5) -> None:
    if keep < 1:
        return
    with _storage_lock():
        autosaves = [item for item in load_snapshots(include_autosaves=True) if item.kind == "autosave"]
        to_delete: list[StoredSnapshot] = []
        if per_window > 0:
            by_window: dict[tuple[Any, str], list[StoredSnapshot]] = {}
            for item in autosaves:
                by_window.setdefault(_autosave_identity(item.data), []).append(item)
            for items in by_window.values():
                to_delete.extend(items[per_window:])

        kept = [item for item in autosaves if item not in to_delete]
        to_delete.extend(kept[keep:])
        seen_paths: set[Path] = set()
        for item in to_delete:
            if item.path in seen_paths:
                continue
            seen_paths.add(item.path)
            _delete_snapshot_unlocked(item)


def prune_expired_autosaves() -> None:
    with _storage_lock():
        now = datetime.now(timezone.utc)
        for item in load_snapshots(include_autosaves=True):
            if item.kind != "autosave":
                continue
            expires_at = str(item.data.get("expires_at") or "")
            if not expires_at:
                continue
            expiry = _parse_timestamp(expires_at)
            if expiry and expiry <= now:
                _delete_snapshot_unlocked(item)


def save_os_window(
    os_window: dict[str, Any],
    name: str,
    *,
    kind: str = "named",
    capture_scrollback: bool = True,
    kitty_target: str | None = None,
) -> Path:
    snapshot = build_snapshot(os_window, name=name, kind=kind, capture_scrollback=capture_scrollback, kitty_target=kitty_target)
    return write_snapshot(snapshot)
