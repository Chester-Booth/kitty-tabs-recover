from __future__ import annotations

import argparse
import difflib
import fcntl
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import hyprland, kitty, popup, session, tui
from .commands import CommandError
from .names import slugify, timestamp
from .snapshot import (
    StoredSnapshot,
    choose_focused_os_window,
    current_workspace_key,
    delete_snapshot,
    load_snapshots,
    mark_autosave_ephemeral,
    prune_autosaves,
    prune_expired_autosaves,
    rename_snapshot,
    save_os_window,
    tab_count,
    window_title,
)


def _print_error(exc: Exception) -> int:
    print(f"ktr: {exc}", file=sys.stderr)
    return 1


def _workspace_summary(item: StoredSnapshot) -> str:
    data = item.data
    tabs = data.get("os_window", {}).get("tabs") or []
    title = data.get("os_window", {}).get("title") or item.name
    updated = data.get("updated_at") or "unknown"
    return f"{item.name:24} {item.kind:8} {len(tabs):2d} tabs  {updated}  {title}"


def cmd_list(args: argparse.Namespace) -> int:
    for item in load_snapshots(include_autosaves=args.autosaves):
        print(_workspace_summary(item))
    return 0


def _save_one(os_window: dict, name: str, *, kind: str, scrollback: bool) -> Path:
    return save_os_window(os_window, name, kind=kind, capture_scrollback=scrollback)


def cmd_save(args: argparse.Namespace) -> int:
    os_windows = kitty.ls()
    if args.all:
        for os_window in os_windows:
            if tab_count(os_window) < 2:
                continue
            name = args.name or window_title(os_window)
            if len(os_windows) > 1:
                name = f"{name}-{os_window.get('id')}"
            path = _save_one(os_window, name, kind="named", scrollback=not args.no_scrollback)
            print(path)
        return 0

    os_window = choose_focused_os_window(os_windows, hyprland.active_window())
    if not os_window:
        raise CommandError("Could not identify the focused kitty OS window")
    name = args.name or window_title(os_window)
    print(_save_one(os_window, name, kind="named", scrollback=not args.no_scrollback))
    return 0


def _match_snapshot(query: str, *, include_autosaves: bool = True) -> StoredSnapshot | None:
    snapshots = load_snapshots(include_autosaves=include_autosaves)
    candidates = snapshots if include_autosaves else [item for item in snapshots if item.kind == "named"]
    if not candidates:
        return None

    query_slug = slugify(query)
    named = [item for item in candidates if item.kind == "named"]
    autosaves = [item for item in candidates if item.kind == "autosave"]
    for scope in (named, autosaves):
        for item in scope:
            if item.name == query or slugify(item.name) == query_slug:
                return item

    prefix = [item for item in candidates if slugify(item.name).startswith(query_slug)]
    if len(prefix) == 1:
        return prefix[0]

    names = [item.name for item in candidates]
    matches = difflib.get_close_matches(query, names, n=2, cutoff=0.45)
    if len(matches) == 1:
        return next(item for item in candidates if item.name == matches[0])
    return None


def _pick_snapshot(include_autosaves: bool) -> StoredSnapshot | None:
    snapshots = load_snapshots(include_autosaves=include_autosaves)
    if not snapshots:
        return None
    while True:
        result = tui.pick_snapshot(snapshots, current_key=current_workspace_key())
        if result.action == "open":
            return result.item
        if result.action == "cancel":
            return None
        if result.action == "rename" and result.item and result.value:
            try:
                renamed = rename_snapshot(result.item, result.value)
            except CommandError as exc:
                print(f"rename failed: {exc}", file=sys.stderr)
                time.sleep(1.5)
                snapshots = load_snapshots(include_autosaves=include_autosaves)
                continue
            snapshots = load_snapshots(include_autosaves=include_autosaves)
            print(f"renamed {result.item.name} to {renamed.name}", file=sys.stderr)
            continue
        if result.action == "delete" and result.item:
            delete_snapshot(result.item)
            snapshots = load_snapshots(include_autosaves=include_autosaves)
            print(f"deleted {result.item.name}", file=sys.stderr)
            if not snapshots:
                return None


def _open_snapshot(item: StoredSnapshot) -> None:
    session_path = session.write_session(item.path)
    session.open_session(session_path)
    print(f"opened {item.name}")


def cmd_reopen(args: argparse.Namespace) -> int:
    if args.name == "*":
        snapshots = [item for item in load_snapshots(include_autosaves=False) if item.kind == "named"]
        if not snapshots:
            raise CommandError("No named workspaces found")
        for item in reversed(snapshots):
            _open_snapshot(item)
        return 0

    include_autosaves = not args.no_autosaves
    item = _match_snapshot(args.name, include_autosaves=include_autosaves) if args.name else _pick_snapshot(include_autosaves)
    if not item:
        raise CommandError("No matching workspace found")
    _open_snapshot(item)
    return 0


def cmd_rename(args: argparse.Namespace) -> int:
    item = _match_snapshot(args.old_name, include_autosaves=True)
    if not item:
        raise CommandError("No matching workspace found")
    renamed = rename_snapshot(item, args.new_name)
    print(renamed.path)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    item = _match_snapshot(args.name, include_autosaves=True)
    if not item:
        raise CommandError("No matching workspace found")
    delete_snapshot(item)
    print(f"deleted {item.name}")
    return 0


def _autosave_name(os_window: dict) -> str:
    digest_source = json.dumps(os_window, sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha1(digest_source).hexdigest()[:8]
    return f"{slugify(window_title(os_window))}-{os_window.get('id', 'window')}-{timestamp()}-{digest}"


def _workspace_name_from_window(os_window: dict) -> str | None:
    for tab in os_window.get("tabs") or []:
        for window in tab.get("windows") or []:
            user_vars = window.get("user_vars") or {}
            env = window.get("env") or {}
            kind = str(user_vars.get("ktr_workspace_kind") or env.get("KTR_WORKSPACE_KIND") or "")
            name = str(user_vars.get("ktr_workspace_name") or env.get("KTR_WORKSPACE_NAME") or "")
            if kind == "named" and name:
                return name
    return None


@contextmanager
def _single_killactive_run():
    path = "/tmp/kitty-tabs-recover-killactive.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def cmd_daemon(args: argparse.Namespace) -> int:
    seen: dict[int, str] = {}
    last_error = ""
    last_error_at = 0.0
    print("ktr daemon: watching kitty multi-tab OS windows")
    while True:
        try:
            os_windows = kitty.ls()
            last_error = ""
            for os_window in os_windows:
                if tab_count(os_window) < args.min_tabs:
                    continue
                comparable = json.dumps(os_window, sort_keys=True, default=str)
                digest = hashlib.sha1(comparable.encode("utf-8")).hexdigest()
                os_id = int(os_window.get("id") or 0)
                if seen.get(os_id) == digest:
                    continue
                seen[os_id] = digest
                path = save_os_window(
                    os_window,
                    _autosave_name(os_window),
                    kind="autosave",
                    capture_scrollback=not args.no_scrollback,
                )
                print(path, flush=True)
                prune_expired_autosaves()
                prune_autosaves(args.keep_autosaves, per_window=args.keep_autosaves_per_window)
        except CommandError as exc:
            now = time.monotonic()
            message = str(exc)
            if message != last_error or now - last_error_at >= args.error_interval:
                print(f"ktr daemon: {exc}", file=sys.stderr, flush=True)
                last_error = message
                last_error_at = now
        time.sleep(args.interval)


def cmd_killactive(args: argparse.Namespace) -> int:
    with _single_killactive_run() as should_run:
        if not should_run:
            return 0
        return _cmd_killactive_locked(args)


def _cmd_killactive_locked(args: argparse.Namespace) -> int:
    active = hyprland.active_window()
    active_address = str(active.get("address") or "") if active else ""
    if not hyprland.is_kitty_window(active):
        hyprland.close_window(active_address)
        return 0

    kitty_target = kitty.target_for_pid(active.get("pid") if active else None)
    os_windows = kitty.ls(target=kitty_target)
    os_window = choose_focused_os_window(os_windows, active)
    if not os_window:
        hyprland.close_window(active_address)
        return 0

    tabs = tab_count(os_window)
    if tabs < 2:
        hyprland.close_window(active_address)
        return 0

    workspace_name = _workspace_name_from_window(os_window)
    if workspace_name:
        save_os_window(os_window, workspace_name, kind="named", capture_scrollback=True, kitty_target=kitty_target)
        hyprland.close_window(active_address)
        return 0

    action = popup.choose_close_action(
        f"This unnamed kitty window has {tabs} tabs.\n\nSave it as a named workspace before closing?"
    )
    if action == "cancel":
        return 0
    if action == "dont-save":
        autosave_path = save_os_window(os_window, _autosave_name(os_window), kind="autosave", capture_scrollback=True, kitty_target=kitty_target)
        mark_autosave_ephemeral(autosave_path, keep_days=7)
        hyprland.close_window(active_address)
        return 0
    if action == "save-as":
        name = popup.ask_name(window_title(os_window))
        if not name:
            return 0
        save_os_window(os_window, name, kind="named", capture_scrollback=True, kitty_target=kitty_target)
    hyprland.close_window(active_address)
    return 0


def cmd_completions(args: argparse.Namespace) -> int:
    if args.shell == "zsh":
        print(
            """#compdef ktr reopen
_ktr_workspaces() {
  local -a names
  names=(${(f)"$(ktr list --autosaves 2>/dev/null | awk '{print $1}')"})
  _describe 'workspaces' names
}

case "$service" in
  reopen) _ktr_workspaces ;;
  ktr)
    local -a commands
    commands=(save list reopen rename delete daemon killactive completions)
    if (( CURRENT == 2 )); then
      _describe 'commands' commands
    elif [[ ${words[2]} == reopen ]]; then
      _ktr_workspaces
    fi
    ;;
esac"""
        )
        return 0
    if args.shell == "bash":
        print(
            """_ktr_complete() {
  local cur="${COMP_WORDS[COMP_CWORD]}"
  local names="$(ktr list --autosaves 2>/dev/null | awk '{print $1}')"
  COMPREPLY=( $(compgen -W "$names" -- "$cur") )
}
complete -F _ktr_complete reopen"""
        )
        return 0
    if args.shell == "fish":
        print("complete -c reopen -a '(ktr list --autosaves 2>/dev/null | awk \"{print \\$1}\")'")
        return 0
    raise CommandError(f"Unsupported shell: {args.shell}")


def build_parser(prog: str = "ktr") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Save, autosave, list and reopen multi-tab kitty workspaces.",
    )
    sub = parser.add_subparsers(dest="command", metavar="command", required=True)

    save = sub.add_parser("save", help="save the focused kitty OS window", description="Save a kitty OS window as a named workspace.")
    save.add_argument("name", nargs="?", help="workspace name; defaults to the active window title")
    save.add_argument("--all", action="store_true", help="save every kitty OS window with enough tabs")
    save.add_argument("--no-scrollback", action="store_true", help="do not capture saved scrollback text")
    save.set_defaults(func=cmd_save)

    list_cmd = sub.add_parser("list", help="list saved workspaces", description="List saved workspaces and, optionally, recovery autosaves.")
    list_cmd.add_argument("--autosaves", action="store_true", help="include recovery autosaves")
    list_cmd.set_defaults(func=cmd_list)

    reopen = sub.add_parser("reopen", help="reopen a workspace", description="Reopen a workspace by name, or show the picker when no name is given.")
    reopen.add_argument("name", nargs="?", help="workspace name, '*' for all saved workspaces, or omit for the picker")
    reopen.add_argument("--no-autosaves", action="store_true", help="only match saved workspaces")
    reopen.set_defaults(func=cmd_reopen)

    rename = sub.add_parser("rename", help="rename a workspace", description="Rename a saved workspace, or promote an autosave by renaming it.")
    rename.add_argument("old_name", help="existing workspace name")
    rename.add_argument("new_name", help="new saved workspace name")
    rename.set_defaults(func=cmd_rename)

    delete = sub.add_parser("delete", help="delete a workspace", description="Delete a saved workspace or recovery autosave.")
    delete.add_argument("name", help="workspace name")
    delete.set_defaults(func=cmd_delete)

    daemon = sub.add_parser("daemon", help="autosave multi-tab kitty windows", description="Watch kitty and autosave OS windows with multiple tabs.")
    daemon.add_argument("--interval", type=float, default=5.0, help="seconds between kitty polls; default: %(default)s")
    daemon.add_argument("--error-interval", type=float, default=60.0, help="minimum seconds between repeated error logs; default: %(default)s")
    daemon.add_argument("--min-tabs", type=int, default=2, help="minimum tab count before autosaving; default: %(default)s")
    daemon.add_argument("--keep-autosaves", type=int, default=50, help="global autosave cap; default: %(default)s")
    daemon.add_argument("--keep-autosaves-per-window", type=int, default=5, help="autosave cap per source window; default: %(default)s")
    daemon.add_argument("--no-scrollback", action="store_true", help="do not capture saved scrollback text")
    daemon.set_defaults(func=cmd_daemon)

    killactive = sub.add_parser(
        "killactive",
        help="Hyprland close helper with save prompt",
        description="Hyprland-only close helper: prompt before closing unnamed multi-tab kitty windows.",
    )
    killactive.set_defaults(func=cmd_killactive)

    completions = sub.add_parser("completions", help="print shell completions", description="Print shell completion code.")
    completions.add_argument("shell", choices=["zsh", "bash", "fish"], help="shell to generate completions for")
    completions.set_defaults(func=cmd_completions)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (CommandError, KeyboardInterrupt) as exc:
        return _print_error(exc)


def reopen_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reopen")
    parser.add_argument("name", nargs="?")
    parser.add_argument("--no-autosaves", action="store_true")
    args = parser.parse_args(argv)
    try:
        return cmd_reopen(args)
    except (CommandError, KeyboardInterrupt) as exc:
        return _print_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
