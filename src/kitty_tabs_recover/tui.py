from __future__ import annotations

import curses
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .snapshot import StoredSnapshot


CTRL_A = "\x01"
CTRL_D = "\x04"
CTRL_G = "\x07"
CTRL_R = "\x12"
CTRL_U = "\x15"
ESC = "\x1b"
FILTER_MODES = ("all", "saved", "recovery")
SORT_MODES = ("updated", "created")
CONTROLS = ("filter", "sort")


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
        if "-" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
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


def _tab_titles(item: StoredSnapshot) -> str:
    titles: list[str] = []
    for index, tab in enumerate(item.data.get("os_window", {}).get("tabs") or []):
        title = str(tab.get("title") or f"Tab {index + 1}").strip()
        if title and title not in titles:
            titles.append(title)
    return ", ".join(titles)


def _cwd_summary(item: StoredSnapshot) -> str:
    tabs = item.data.get("os_window", {}).get("tabs") or []
    cwds: list[str] = []
    for tab in tabs:
        for window in tab.get("windows") or []:
            cwd = window.get("cwd")
            if cwd and cwd not in cwds:
                cwds.append(str(cwd))
    return ", ".join(cwds[:2])


def _type_label(item: StoredSnapshot) -> str:
    return "recovery" if item.kind == "autosave" else "save"


def _row_fields(item: StoredSnapshot) -> tuple[str, str, str, str, str]:
    tabs = item.data.get("os_window", {}).get("tabs") or []
    return (
        _age(item.data.get("updated_at")),
        item.name,
        str(len(tabs)),
        _type_label(item),
        _tab_titles(item) or str(item.data.get("os_window", {}).get("title") or ""),
    )


def _row(item: StoredSnapshot, *, is_current: bool, connector: str = "") -> str:
    date, name, tabs, type_label, tab_titles = _row_fields(item)
    current = " current" if is_current else ""
    return f"{date:<9} {connector:<1} {name:<28.28} {tabs:>4} {type_label:<8} {tab_titles}{current}"


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
        curses.set_escdelay(25)
        curses.curs_set(1)
        curses.raw()
    except curses.error:
        pass
    if curses.has_colors():
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_WHITE, -1)
        curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_BLACK)
        curses.init_pair(6, curses.COLOR_BLUE, -1)
        curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLACK)
        curses.init_pair(8, curses.COLOR_MAGENTA, -1)
    selected = 0
    query = ""
    filter_mode = "all"
    sort_mode = "updated"
    active_control = "filter"
    expanded_autosave_key: tuple[object, str] | None = None
    status_message = ""

    while True:
        visible_items = _visible_items(items, query, filter_mode, sort_mode, expanded_autosave_key)
        if selected >= len(visible_items):
            selected = max(0, len(visible_items) - 1)

        stdscr.erase()
        height, width = stdscr.getmaxyx()
        _draw_header(stdscr, width, query, filter_mode, sort_mode, active_control, expanded_autosave_key)

        list_top = 5
        list_height = max(1, height - list_top - 3)
        selected_item = visible_items[selected] if visible_items else None
        visible_items = _visible_items(items, query, filter_mode, sort_mode, expanded_autosave_key)
        if selected_item and selected_item in visible_items:
            selected = visible_items.index(selected_item)
        elif selected >= len(visible_items):
            selected = max(0, len(visible_items) - 1)

        rows = _display_rows(visible_items)
        selected_display_index = _display_index(rows, selected)
        offset = max(0, selected_display_index - list_height + 1) if selected_display_index >= list_height else 0
        visible_rows = rows[offset : offset + list_height]
        for row_index, row in enumerate(visible_rows):
            y = list_top + row_index
            absolute_index = offset + row_index
            if row.kind == "heading":
                stdscr.addnstr(y, 0, row.label, width - 1, _colour(4) | curses.A_BOLD)
                continue
            item = row.item
            if item is None:
                continue
            item_selected = absolute_index == selected_display_index
            is_current = _is_current(item, current_key)
            attr = _row_attr(item_selected, is_current, row_index)
            prefix = "> " if item_selected else "  "
            connector = _autosave_connector(item, expanded_autosave_key)
            stdscr.addnstr(y, 0, prefix + _row(item, is_current=is_current, connector=connector), width - 1, attr)

        _draw_footer(stdscr, height, width, selected, len(visible_items), len(items), status_message)
        stdscr.move(2, min(width - 1, len("Type to search  ") + len(query)))
        stdscr.refresh()

        key = _read_key(stdscr)
        if key in ("\n", "\r", curses.KEY_ENTER):
            return TuiResult("open", visible_items[selected]) if visible_items else TuiResult("cancel")
        if key in (ESC, "\x03"):
            return TuiResult("cancel")
        if key == curses.KEY_UP:
            selected = max(0, selected - 1)
        elif key == curses.KEY_DOWN:
            selected = min(max(0, len(visible_items) - 1), selected + 1)
        elif key == "\t":
            active_control = CONTROLS[(CONTROLS.index(active_control) + 1) % len(CONTROLS)]
        elif key == curses.KEY_BTAB:
            active_control = CONTROLS[(CONTROLS.index(active_control) - 1) % len(CONTROLS)]
        elif key == curses.KEY_LEFT:
            if active_control == "filter":
                filter_mode = _previous_filter(filter_mode)
            else:
                sort_mode = _previous_sort(sort_mode)
            selected = 0
        elif key == curses.KEY_RIGHT:
            if active_control == "filter":
                filter_mode = _next_filter(filter_mode)
            else:
                sort_mode = _next_sort(sort_mode)
            selected = 0
        elif key == CTRL_A and visible_items:
            item = visible_items[selected]
            if item.kind == "autosave":
                key_for_item = _autosave_key(item)
                if expanded_autosave_key == key_for_item:
                    expanded_autosave_key = None
                    status_message = "showing latest autosave per window"
                else:
                    expanded_autosave_key = key_for_item
                    status_message = "showing autosaves for selected window"
            else:
                status_message = "select an autosave row to expand its history"
        elif key == CTRL_R and visible_items:
            name = _prompt(stdscr, "Rename to: ")
            if name:
                return TuiResult("rename", visible_items[selected], name)
            status_message = "rename cancelled"
        elif key == CTRL_D and visible_items:
            if _confirm(stdscr, f"Delete {visible_items[selected].name}? y/N "):
                return TuiResult("delete", visible_items[selected])
            status_message = "delete cancelled"
        elif key == CTRL_G and current_key is not None:
            current_item = next((item for item in items if _is_current(item, current_key)), None)
            if current_item:
                filter_mode = "saved" if current_item.kind == "named" else "recovery"
                query = ""
                scoped = _visible_items(
                    items,
                    query,
                    filter_mode,
                    sort_mode,
                    _autosave_key(current_item) if current_item.kind == "autosave" else None,
                )
                selected = scoped.index(current_item) if current_item in scoped else 0
        elif key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            query = query[:-1]
            selected = 0
            status_message = ""
        elif key == CTRL_U:
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
            _tab_titles(item),
            _cwd_summary(item),
        ]
    ).lower()
    return all(part in haystack for part in query.lower().split())


def _autosave_key(item: StoredSnapshot) -> tuple[object, str]:
    os_window = item.data.get("os_window") or {}
    title = str(os_window.get("title") or "")
    name = re.sub(r"-\d+-\d{8}T\d{6}Z-[0-9a-f]+$", "", item.name)
    return os_window.get("id"), title or name


def _autosaves_for_view(items: list[StoredSnapshot], expanded_key: tuple[object, str] | None) -> list[StoredSnapshot]:
    visible: list[StoredSnapshot] = []
    collapsed_keys: set[tuple[object, str]] = set()
    for item in _sort_items([item for item in items if item.kind == "autosave"], "updated"):
        key = _autosave_key(item)
        if key == expanded_key:
            visible.append(item)
        elif key not in collapsed_keys:
            visible.append(item)
            collapsed_keys.add(key)
    return visible


def _visible_items(
    items: list[StoredSnapshot],
    query: str,
    filter_mode: str,
    sort_mode: str,
    expanded_autosave_key: tuple[object, str] | None,
) -> list[StoredSnapshot]:
    named = [item for item in items if item.kind == "named"]
    autosaves = _autosaves_for_view(items, expanded_autosave_key)
    if filter_mode == "saved":
        scoped = named
    elif filter_mode == "recovery":
        scoped = autosaves
    else:
        scoped = named + autosaves
    return _sort_visible([item for item in scoped if _matches(item, query)], sort_mode, expanded_autosave_key)


def _sort_items(items: list[StoredSnapshot], sort_mode: str) -> list[StoredSnapshot]:
    key_name = "created_at" if sort_mode == "created" else "updated_at"
    return sorted(items, key=lambda item: _parse_time(item.data.get(key_name)) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def _sort_visible(items: list[StoredSnapshot], sort_mode: str, expanded_key: tuple[object, str] | None) -> list[StoredSnapshot]:
    sorted_items = _sort_items(items, sort_mode)
    if expanded_key is None:
        return sorted_items
    expanded = [item for item in sorted_items if item.kind == "autosave" and _autosave_key(item) == expanded_key]
    if len(expanded) < 2:
        return sorted_items
    first_index = next((index for index, item in enumerate(sorted_items) if item in expanded), None)
    if first_index is None:
        return sorted_items
    collapsed = [item for item in sorted_items if item not in expanded]
    return collapsed[:first_index] + expanded + collapsed[first_index:]


def _display_rows(items: list[StoredSnapshot]) -> list[DisplayRow]:
    rows = [DisplayRow("heading", _heading())]
    rows.extend(DisplayRow("item", "", item) for item in items)
    return rows


def _heading() -> str:
    return f"  {'date':<9}   {'name':<28} {'tabs':>4} {'type':<8} tab titles"


def _autosave_connector(item: StoredSnapshot, expanded_key: tuple[object, str] | None) -> str:
    if item.kind == "autosave" and expanded_key is not None and _autosave_key(item) == expanded_key:
        return "│"
    return ""


def _display_index(rows: list[DisplayRow], selected: int) -> int:
    item_index = -1
    for row_index, row in enumerate(rows):
        if row.kind != "item":
            continue
        item_index += 1
        if item_index == selected:
            return row_index
    return 1 if len(rows) > 1 else 0


def _next_filter(filter_mode: str) -> str:
    index = FILTER_MODES.index(filter_mode)
    return FILTER_MODES[(index + 1) % len(FILTER_MODES)]


def _previous_filter(filter_mode: str) -> str:
    index = FILTER_MODES.index(filter_mode)
    return FILTER_MODES[(index - 1) % len(FILTER_MODES)]


def _next_sort(sort_mode: str) -> str:
    index = SORT_MODES.index(sort_mode)
    return SORT_MODES[(index + 1) % len(SORT_MODES)]


def _previous_sort(sort_mode: str) -> str:
    index = SORT_MODES.index(sort_mode)
    return SORT_MODES[(index - 1) % len(SORT_MODES)]


def _draw_header(
    stdscr: curses.window,
    width: int,
    query: str,
    filter_mode: str,
    sort_mode: str,
    active_control: str,
    expanded_autosave_key: tuple[object, str] | None,
) -> None:
    stdscr.addnstr(0, 0, "Resume a kitty workspace", width - 1, _colour(1) | curses.A_BOLD)
    stdscr.addnstr(2, 0, "Type to search  ", width - 1, curses.A_BOLD)
    stdscr.addnstr(2, len("Type to search  "), query, max(0, width - len("Type to search  ") - 1))
    right_segments = [
        ("Filter: ", curses.A_DIM),
        *_control_segments(FILTER_MODES, filter_mode, active_control == "filter"),
        ("    Sort: ", curses.A_DIM),
        *_control_segments(SORT_MODES, sort_mode, active_control == "sort"),
    ]
    if expanded_autosave_key is not None:
        right_segments.extend([("    Autosaves: ", curses.A_DIM), ("[Window]", _colour(4) | curses.A_BOLD)])
    right = "".join(text for text, _attr in right_segments)
    _draw_segments(stdscr, 2, max(0, width - len(right) - 1), right_segments, width)


def _control_segments(options: tuple[str, ...], active: str, focused: bool) -> list[tuple[str, int]]:
    segments: list[tuple[str, int]] = []
    for option in options:
        label = "Saved" if option == "saved" else "Recovery" if option == "recovery" else option.capitalize()
        text = f"[{label}]" if option == active else label
        attr = curses.A_DIM
        if option == active:
            attr = (_colour(8) if focused else _colour(4)) | curses.A_BOLD
        segments.append((text, attr))
        if option != options[-1]:
            segments.append((" ", curses.A_DIM))
    return segments


def _draw_segments(stdscr: curses.window, y: int, x: int, segments: list[tuple[str, int]], width: int) -> None:
    remaining = max(0, width - x - 1)
    cursor = x
    for text, attr in segments:
        if remaining <= 0:
            break
        stdscr.addnstr(y, cursor, text, remaining, attr)
        written = min(len(text), remaining)
        cursor += written
        remaining -= written


def _draw_footer(stdscr: curses.window, height: int, width: int, selected: int, count: int, total: int, status_message: str) -> None:
    y = height - 2
    status = f"{selected + 1 if count else 0} / {count} of {total}"
    divider_width = max(0, width - 1)
    divider = f" {status} "
    if divider_width > len(divider):
        divider = "─" * (divider_width - len(divider) - 1) + divider + "─"
    stdscr.addnstr(y - 1, 0, divider, divider_width, curses.A_DIM)
    _draw_segments(stdscr, y, 0, _footer_segments(), width)
    if status_message:
        stdscr.addnstr(y + 1 if y + 1 < height else y, 0, status_message, width - 1, _colour(2))


def _footer_segments() -> list[tuple[str, int]]:
    key = _colour(4)
    action = curses.A_DIM
    return [
        ("enter", key),
        (" reopen   ", action),
        ("tab", key),
        (" focus filter/sort   ", action),
        ("←/→", key),
        (" change   ", action),
        ("ctrl+a", key),
        (" recovery for row   ", action),
        ("ctrl+r", key),
        (" rename   ", action),
        ("ctrl+d", key),
        (" delete   ", action),
        ("esc", key),
        (" cancel", action),
    ]


def _colour(pair: int) -> int:
    if not curses.has_colors():
        return curses.A_NORMAL
    return curses.color_pair(pair)


def _read_key(stdscr: curses.window) -> object:
    try:
        return stdscr.get_wch()
    except KeyboardInterrupt:
        return "\x03"


def _row_attr(selected: bool, current: bool, row_index: int) -> int:
    if selected:
        return _colour(2) | curses.A_BOLD
    if current:
        return _colour(4) | curses.A_BOLD
    if row_index % 2 == 1:
        return _colour(7)
    return curses.A_DIM if curses.has_colors() else curses.A_NORMAL


def _is_current(item: StoredSnapshot, current_key: tuple[str, str] | None) -> bool:
    if not current_key:
        return False
    return item.kind == current_key[0] and item.name == current_key[1]


def _prompt(stdscr: curses.window, label: str) -> str | None:
    try:
        curses.set_escdelay(25)
    except curses.error:
        pass
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
        key = _read_key(stdscr)
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
    try:
        curses.set_escdelay(25)
    except curses.error:
        pass
    height, width = stdscr.getmaxyx()
    y = height - 1
    stdscr.move(y, 0)
    stdscr.clrtoeol()
    stdscr.addnstr(y, 0, prompt, width - 1, _colour(2))
    stdscr.refresh()
    key = _read_key(stdscr)
    if key in (ESC, "\x03"):
        return False
    return key in ("y", "Y")
