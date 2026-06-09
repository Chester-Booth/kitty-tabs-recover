from __future__ import annotations

import curses
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .snapshot import StoredSnapshot


CTRL_D = "\x04"
CTRL_F = "\x06"
CTRL_G = "\x07"
CTRL_R = "\x12"
CTRL_U = "\x15"
ESC = "\x1b"
FILTER_MODES = ("all", "saved", "archived")


@dataclass(frozen=True)
class TuiResult:
    action: str
    item: StoredSnapshot | None = None
    value: str | None = None


@dataclass(frozen=True)
class DisplayRow:
    kind: str
    label: str
    item: StoredSnapshot | None = None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age(value: object) -> str:
    parsed = _parse_time(value)
    if not parsed:
        return "unknown"
    delta = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _cwd_summary(item: StoredSnapshot) -> str:
    tabs = item.data.get("os_window", {}).get("tabs") or []
    cwds: list[str] = []
    for tab in tabs:
        for window in tab.get("windows") or []:
            cwd = window.get("cwd")
            if cwd and cwd not in cwds:
                cwds.append(str(cwd))
    return ", ".join(cwds[:2])


def _row(item: StoredSnapshot, *, is_current: bool) -> str:
    tabs = item.data.get("os_window", {}).get("tabs") or []
    title = item.data.get("os_window", {}).get("title") or item.name
    cwd = _cwd_summary(item)
    marker = "auto" if item.kind == "autosave" else "save"
    current = "current" if is_current else ""
    return f"{_age(item.data.get('updated_at')):<8} {marker:<4} {current:<7} {len(tabs):>2} tabs  {item.name}  {title}  {cwd}"


def pick_snapshot(snapshots: Iterable[StoredSnapshot], *, current_key: tuple[str, str] | None = None) -> TuiResult:
    items = list(snapshots)
    if not items:
        return TuiResult("cancel")
    try:
        return curses.wrapper(_run, items, current_key)
    except curses.error:
        return _fallback(items, current_key)


def _fallback(items: list[StoredSnapshot], current_key: tuple[str, str] | None) -> TuiResult:
    for index, item in enumerate(items, start=1):
        print(f"{index:2d}. {_row(item, is_current=_is_current(item, current_key))}")
    answer = input("Reopen workspace: ").strip()
    if not answer:
        return TuiResult("cancel")
    try:
        index = int(answer)
    except ValueError:
        return TuiResult("cancel")
    if 1 <= index <= len(items):
        return TuiResult("open", items[index - 1])
    return TuiResult("cancel")


def _run(stdscr: curses.window, items: list[StoredSnapshot], current_key: tuple[str, str] | None) -> TuiResult:
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    if curses.has_colors():
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)
    selected = 0
    query = ""
    filter_mode = "all"
    status_message = ""
    current_index = _current_index(items, current_key)
    if current_index is not None:
        selected = current_index

    while True:
        filtered = _filtered_items(items, query, filter_mode)
        if selected >= len(filtered):
            selected = max(0, len(filtered) - 1)

        stdscr.erase()
        height, width = stdscr.getmaxyx()
        _draw_header(stdscr, width, query, filter_mode)

        list_top = 4
        list_height = max(1, height - list_top - 3)
        display_rows = _display_rows(filtered, filter_mode)
        selected_display_index = _display_index(display_rows, selected)
        offset = 0
        if selected_display_index >= list_height:
            offset = selected_display_index - list_height + 1
        visible = display_rows[offset : offset + list_height]
        for row_index, row in enumerate(visible):
            y = list_top + row_index
            item_index = offset + row_index
            if row.kind == "divider":
                stdscr.addnstr(y, 0, row.label, width - 1, curses.A_DIM)
                continue
            item = row.item
            if item is None:
                continue
            item_selected = item_index == selected_display_index
            text = _row(item, is_current=_is_current(item, current_key))
            prefix = "> " if item_selected else "  "
            attr = _colour(3) if item_selected else (_colour(2) if _is_current(item, current_key) else curses.A_NORMAL)
            stdscr.addnstr(y, 0, prefix + text, width - 1, attr)

        _draw_footer(stdscr, height, width, selected, len(filtered), len(items), status_message)
        stdscr.move(2, min(width - 1, len("Type to search  ") + len(query)))
        stdscr.refresh()

        key = stdscr.get_wch()
        if key in ("\n", "\r", curses.KEY_ENTER):
            return TuiResult("open", filtered[selected]) if filtered else TuiResult("cancel")
        if key in (ESC, "\x03"):
            return TuiResult("cancel")
        if key == curses.KEY_UP:
            selected = max(0, selected - 1)
        elif key == curses.KEY_DOWN:
            selected = min(max(0, len(filtered) - 1), selected + 1)
        elif key == curses.KEY_LEFT:
            filter_mode = _previous_filter(filter_mode)
            selected = 0
        elif key == curses.KEY_RIGHT or key == CTRL_F:
            filter_mode = _next_filter(filter_mode)
            selected = 0
        elif key == CTRL_R and filtered:
            name = _prompt(stdscr, "Rename to: ")
            if name:
                return TuiResult("rename", filtered[selected], name)
            status_message = "rename cancelled"
        elif key == CTRL_D and filtered:
            if _confirm(stdscr, f"Delete {filtered[selected].name}? y/N "):
                return TuiResult("delete", filtered[selected])
            status_message = "delete cancelled"
        elif key == CTRL_G and current_index is not None:
            current_item = items[current_index]
            filter_mode = "saved" if current_item.kind == "named" else "archived"
            filtered = _filtered_items(items, "", filter_mode)
            selected = next((index for index, item in enumerate(filtered) if item == current_item), 0)
            query = ""
        elif key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            query = query[:-1]
            selected = 0
            status_message = ""
        elif key == "\t" or key == CTRL_U:
            query = ""
            selected = 0
            status_message = ""
        elif isinstance(key, str) and key.isprintable():
            query += key
            selected = 0
            status_message = ""


def _matches(item: StoredSnapshot, query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        [
            item.name,
            item.kind,
            str(item.data.get("os_window", {}).get("title") or ""),
            _cwd_summary(item),
        ]
    ).lower()
    return all(part in haystack for part in query.lower().split())


def _filtered_items(items: list[StoredSnapshot], query: str, filter_mode: str) -> list[StoredSnapshot]:
    if filter_mode == "saved":
        scoped = [item for item in items if item.kind == "named"]
    elif filter_mode == "archived":
        scoped = [item for item in items if item.kind != "named"]
    else:
        scoped = list(items)
    return [item for item in scoped if _matches(item, query)]


def _display_rows(items: list[StoredSnapshot], filter_mode: str) -> list[DisplayRow]:
    if filter_mode != "all":
        return [DisplayRow("item", "", item) for item in items]

    saved = [item for item in items if item.kind == "named"]
    archived = [item for item in items if item.kind != "named"]
    rows: list[DisplayRow] = []
    if saved:
        rows.append(DisplayRow("divider", "-- saved workspaces --"))
        rows.extend(DisplayRow("item", "", item) for item in saved)
    if archived:
        rows.append(DisplayRow("divider", "-- autosaved / archived workspaces --"))
        rows.extend(DisplayRow("item", "", item) for item in archived)
    return rows


def _display_index(rows: list[DisplayRow], selected: int) -> int:
    item_index = -1
    for row_index, row in enumerate(rows):
        if row.kind != "item":
            continue
        item_index += 1
        if item_index == selected:
            return row_index
    return 0


def _next_filter(filter_mode: str) -> str:
    index = FILTER_MODES.index(filter_mode)
    return FILTER_MODES[(index + 1) % len(FILTER_MODES)]


def _previous_filter(filter_mode: str) -> str:
    index = FILTER_MODES.index(filter_mode)
    return FILTER_MODES[(index - 1) % len(FILTER_MODES)]


def _draw_header(stdscr: curses.window, width: int, query: str, filter_mode: str) -> None:
    stdscr.addnstr(0, 0, "Resume a kitty workspace", width - 1, _colour(1) | curses.A_BOLD)
    stdscr.addnstr(2, 0, "Type to search  ", width - 1, curses.A_BOLD)
    stdscr.addnstr(2, len("Type to search  "), query, max(0, width - len("Type to search  ") - 1))
    filter_label = filter_mode.capitalize()
    right = f"Filter: [{filter_label}] All Saved Archived    Sort: Updated"
    stdscr.addnstr(2, max(0, width - len(right) - 1), right, len(right), _colour(4))


def _draw_footer(stdscr: curses.window, height: int, width: int, selected: int, count: int, total: int, status_message: str) -> None:
    y = height - 2
    stdscr.hline(y - 1, 0, curses.ACS_HLINE, width - 1)
    status = f"{selected + 1 if count else 0} / {count} of {total}"
    stdscr.addnstr(y - 1, max(0, width - len(status) - 1), status, len(status))
    footer = "enter reopen   ctrl+r rename   ctrl+d delete   ctrl+g current   left/right filter   esc cancel"
    stdscr.addnstr(y, 0, footer, width - 1)
    if status_message:
        stdscr.addnstr(y + 1 if y + 1 < height else y, 0, status_message, width - 1, _colour(2))


def _colour(pair: int) -> int:
    if not curses.has_colors():
        return curses.A_NORMAL
    return curses.color_pair(pair)


def _is_current(item: StoredSnapshot, current_key: tuple[str, str] | None) -> bool:
    if not current_key:
        return False
    return item.kind == current_key[0] and item.name == current_key[1]


def _current_index(items: list[StoredSnapshot], current_key: tuple[str, str] | None) -> int | None:
    for index, item in enumerate(items):
        if _is_current(item, current_key):
            return index
    return None


def _prompt(stdscr: curses.window, label: str) -> str | None:
    height, width = stdscr.getmaxyx()
    y = height - 1
    value = ""
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    while True:
        stdscr.move(y, 0)
        stdscr.clrtoeol()
        stdscr.addnstr(y, 0, label + value, width - 1, _colour(2))
        stdscr.move(y, min(width - 1, len(label) + len(value)))
        stdscr.refresh()
        key = stdscr.get_wch()
        if key in ("\n", "\r", curses.KEY_ENTER):
            value = value.strip()
            return value or None
        if key in (ESC, "\x03"):
            return None
        if key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            value = value[:-1]
        elif key == CTRL_U:
            value = ""
        elif isinstance(key, str) and key.isprintable():
            value += key


def _confirm(stdscr: curses.window, prompt: str) -> bool:
    height, width = stdscr.getmaxyx()
    y = height - 1
    stdscr.move(y, 0)
    stdscr.clrtoeol()
    stdscr.addnstr(y, 0, prompt, width - 1, _colour(2))
    stdscr.refresh()
    key = stdscr.get_wch()
    return key in ("y", "Y")
