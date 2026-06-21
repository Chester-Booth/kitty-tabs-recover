from __future__ import annotations

import json
import os
import curses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kitty_tabs_recover import kitty
from kitty_tabs_recover.names import slugify
from kitty_tabs_recover.session import _history_commands, _restore_message, _trim_trailing_prompt, render_session
from kitty_tabs_recover.snapshot import current_workspace_key, delete_snapshot, load_snapshots, mark_autosave_ephemeral, prune_autosaves, rename_snapshot, write_snapshot
from kitty_tabs_recover.tui import _autosave_key, _display_rows, _draw_header, _ellipsise, _footer_segments, _row_for_width, _search_segments, _visible_items


class FakeScreen:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str, int]] = []

    def addnstr(self, y: int, x: int, text: str, _width: int, attr: int = 0) -> None:
        self.calls.append((y, x, text, attr))

    def line(self, y: int) -> str:
        return "".join(text for call_y, _x, text, _attr in sorted(self.calls, key=lambda call: call[1]) if call_y == y)


def sample_snapshot(name: str = "Project") -> dict:
    return {
        "schema_version": 1,
        "name": name,
        "kind": "named",
        "created_at": "2026-06-08T00:00:00Z",
        "updated_at": "2026-06-08T00:00:00Z",
        "os_window": {
            "id": 1,
            "title": "Project",
            "tabs": [
                {
                    "index": 0,
                    "id": 10,
                    "title": "api",
                    "layout": "tall",
                    "is_active": False,
                    "windows": [
                        {
                            "index": 0,
                            "id": 100,
                            "title": "api",
                            "cwd": "/tmp/api",
                            "cmdline": ["zsh"],
                            "is_active": True,
                            "scrollback": "hello\n",
                        }
                    ],
                },
                {
                    "index": 1,
                    "id": 11,
                    "title": "web",
                    "layout": None,
                    "is_active": True,
                    "windows": [
                        {
                            "index": 0,
                            "id": 101,
                            "title": "web",
                            "cwd": "/tmp/web",
                            "cmdline": ["zsh"],
                            "is_active": True,
                            "scrollback": "",
                        }
                    ],
                },
            ],
        },
    }


class CoreTests(unittest.TestCase):
    def test_slugify(self) -> None:
        self.assertEqual(slugify("My Project!"), "my-project")
        self.assertEqual(slugify(""), "workspace")

    def test_write_snapshot_splits_scrollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                path = write_snapshot(sample_snapshot())
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["name"], "Project")
                scrollback = path.parent / "scrollback/tab-001-window-001.txt"
                self.assertEqual(scrollback.read_text(encoding="utf-8"), "hello\n")
                self.assertEqual(data["os_window"]["tabs"][0]["windows"][0]["scrollback_file"], "scrollback/tab-001-window-001.txt")

    def test_render_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scrollback").mkdir()
            (root / "scrollback/tab-001-window-001.txt").write_text("❯ gs\n╭─ prompt\n╰─ battery\n", encoding="utf-8")
            snapshot = sample_snapshot()
            first_window = snapshot["os_window"]["tabs"][0]["windows"][0]
            first_window.pop("scrollback")
            first_window["scrollback_file"] = "scrollback/tab-001-window-001.txt"
            rendered = render_session(snapshot, snapshot_root=root)
            self.assertIn("os_window_title Project", rendered)
            self.assertNotIn("tab_title", rendered)
            self.assertIn("new_tab web", rendered)
            self.assertIn("--env=KTR_WORKSPACE_NAME=Project", rendered)
            self.assertIn("--env=KTR_WORKSPACE_KIND=named", rendered)
            self.assertIn("--env=KTR_WORKSPACE_SLUG=project", rendered)
            self.assertIn("--var=ktr_workspace_name=Project", rendered)
            self.assertIn("--var=ktr_workspace_kind=named", rendered)
            self.assertIn("--var=ktr_workspace_slug=project", rendered)
            self.assertIn("new_tab api", rendered)
            self.assertIn("--tab-title=api --title=api --cwd=/tmp/api --var=ktr_focus=1 --env=HISTFILE=", rendered)
            self.assertIn(str(root / "restore/tab-001-window-001.txt"), rendered)
            self.assertIn(str(root / "history/tab-001-window-001.zsh_history"), rendered)
            self.assertNotIn("Restored", (root / "restore/tab-001-window-001.txt").read_text())
            self.assertIn("Project '01:00 AM on 08/06/2026'", rendered)
            self.assertIn(": ", (root / "history/tab-001-window-001.zsh_history").read_text())
            self.assertIn("focus_tab 1", rendered)

    def test_render_session_does_not_quote_tab_titles_with_spaces(self) -> None:
        snapshot = sample_snapshot("Project")
        snapshot["os_window"]["title"] = "learn guide"
        snapshot["os_window"]["tabs"][0]["title"] = "cd learn"
        snapshot["os_window"]["tabs"][0]["windows"][0]["title"] = "cd learn"
        snapshot["os_window"]["tabs"][1]["title"] = "cd small"
        snapshot["os_window"]["tabs"][1]["windows"][0]["title"] = "cd small"

        rendered = render_session(snapshot)

        self.assertIn("os_window_title learn guide", rendered)
        self.assertIn("new_tab cd learn", rendered)
        self.assertIn("new_tab cd small", rendered)
        self.assertNotIn("new_tab 'cd learn'", rendered)
        self.assertNotIn("new_tab 'cd small'", rendered)

    def test_restore_scrollback_trims_empty_p10k_prompt(self) -> None:
        raw = "output\n\x1b[38:5:242m╭─ fancy prompt\n\x1b[38:5:242m╰─ battery\n"
        self.assertEqual(_trim_trailing_prompt(raw), "output\n")

    def test_history_commands_are_extracted_from_scrollback(self) -> None:
        raw = "\x1b[32m❯\x1b[39m gs\nblox ~/repo main $ git diff --stat\nplain output\n"
        self.assertEqual(_history_commands(raw), ["gs", "git diff --stat"])

    def test_restore_message_format(self) -> None:
        self.assertRegex(_restore_message(sample_snapshot()), r"^Restored Project from \d\d:\d\d [AP]M on \d\d/\d\d/\d\d\d\d$")

    def test_prune_autosaves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                for index in range(3):
                    snapshot = sample_snapshot(f"auto-{index}")
                    snapshot["kind"] = "autosave"
                    snapshot["updated_at"] = f"2026-06-08T00:00:0{index}Z"
                    write_snapshot(snapshot)
                prune_autosaves(1)
                snapshots = load_snapshots(include_autosaves=True)
                self.assertEqual([item.name for item in snapshots if item.kind == "autosave"], ["auto-2"])

    def test_mark_autosave_ephemeral_keeps_latest_for_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                first = sample_snapshot("first")
                first["kind"] = "autosave"
                first["os_window"]["id"] = 7
                first["os_window"]["title"] = "same"
                write_snapshot(first)

                second = sample_snapshot("second")
                second["kind"] = "autosave"
                second["os_window"]["id"] = 7
                second["os_window"]["title"] = "same"
                second_path = write_snapshot(second)

                mark_autosave_ephemeral(second_path)
                autosaves = [item for item in load_snapshots(include_autosaves=True) if item.kind == "autosave"]
                self.assertEqual([item.name for item in autosaves], ["second"])
                self.assertIn("expires_at", autosaves[0].data)

    def test_rename_promotes_autosave_and_delete_removes_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                snapshot = sample_snapshot("auto")
                snapshot["kind"] = "autosave"
                path = write_snapshot(snapshot)
                item = load_snapshots(include_autosaves=True)[0]
                self.assertEqual(item.kind, "autosave")

                renamed = rename_snapshot(item, "renamed")
                self.assertEqual(renamed.kind, "named")
                self.assertEqual(renamed.name, "renamed")
                self.assertFalse(path.parent.exists())
                self.assertTrue(renamed.path.exists())

                delete_snapshot(renamed)
                self.assertFalse(renamed.path.parent.exists())

    def test_current_workspace_key(self) -> None:
        with mock.patch.dict(os.environ, {"KTR_WORKSPACE_NAME": "Project", "KTR_WORKSPACE_KIND": "named"}):
            self.assertEqual(current_workspace_key(), ("named", "Project"))

    def test_get_scrollback_requests_ansi(self) -> None:
        calls = []

        def fake_run(argv, *, check=True, input_text=None):
            calls.append(argv)
            return mock.Mock(returncode=0, stdout="\x1b[31mred\x1b[0m\n")

        with mock.patch("kitty_tabs_recover.kitty._socket_candidates", return_value=["unix:/tmp/kitty"]), mock.patch(
            "kitty_tabs_recover.kitty.run", side_effect=fake_run
        ), mock.patch("kitty_tabs_recover.kitty.require_binary", return_value="kitten"):
            self.assertEqual(kitty.get_scrollback(12), "\x1b[31mred\x1b[0m\n")

        self.assertIn("--ansi", calls[0])

    def test_tui_filters_and_latest_autosaves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                saved = sample_snapshot("saved")
                write_snapshot(saved)
                auto = sample_snapshot("auto")
                auto["kind"] = "autosave"
                auto["updated_at"] = "2026-06-08T00:00:00Z"
                auto["os_window"]["id"] = 9
                auto["os_window"]["title"] = "same-window"
                write_snapshot(auto)
                newer_auto = sample_snapshot("newer-auto")
                newer_auto["kind"] = "autosave"
                newer_auto["updated_at"] = "2026-06-08T00:01:00Z"
                newer_auto["os_window"]["id"] = 9
                newer_auto["os_window"]["title"] = "same-window"
                write_snapshot(newer_auto)
                items = load_snapshots(include_autosaves=True)

                self.assertEqual([item.kind for item in _visible_items(items, "", "saved", "updated", None)], ["named"])
                recovery = _visible_items(items, "", "recovery", "updated", None)
                self.assertEqual([item.name for item in recovery], ["newer-auto"])

                all_autosaves = _visible_items(items, "", "recovery", "updated", _autosave_key(recovery[0]))
                self.assertEqual([item.name for item in all_autosaves], ["newer-auto", "auto"])

                rows = _display_rows(_visible_items(items, "", "all", "updated", None))
                self.assertEqual(rows[0].kind, "heading")
                self.assertIn("date", rows[0].label)

    def test_tui_row_truncates_tab_titles_with_ellipsis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                snapshot = sample_snapshot("long-tabs")
                snapshot["os_window"]["tabs"][0]["title"] = "a very long api tab title"
                snapshot["os_window"]["tabs"][1]["title"] = "a very long web tab title"
                write_snapshot(snapshot)
                item = load_snapshots(include_autosaves=True)[0]

                row = _row_for_width(item, 72, is_current=False)

                self.assertLessEqual(len(row), 71)
                self.assertTrue(row.endswith("..."))

    def test_tui_footer_hides_purposes_before_keys_when_narrow(self) -> None:
        with mock.patch("curses.has_colors", return_value=False):
            wide = "".join(text for text, _attr in _footer_segments(140))
            narrow = "".join(text for text, _attr in _footer_segments(40))

        self.assertIn("reopen", wide)
        self.assertIn("focus filter/sort", wide)
        self.assertNotIn("reopen", narrow)
        self.assertNotIn("focus filter/sort", narrow)
        self.assertIn("enter", narrow)
        self.assertIn("tab", narrow)

    def test_tui_ellipsise_handles_tiny_widths(self) -> None:
        self.assertEqual(_ellipsise("abcdef", 0), "")
        self.assertEqual(_ellipsise("abcdef", 2), "..")
        self.assertEqual(_ellipsise("abcdef", 5), "ab...")

    def test_tui_header_keeps_search_and_controls_on_one_row_when_they_fit(self) -> None:
        screen = FakeScreen()

        with mock.patch("curses.has_colors", return_value=False):
            list_top = _draw_header(screen, 120, "", "all", "updated", "filter", None)

        self.assertEqual(list_top, 4)
        self.assertIn("Type to search", screen.line(2))
        self.assertIn("Filter:", screen.line(2))
        self.assertNotIn("Filter:", screen.line(3))

    def test_tui_header_wraps_controls_when_search_row_is_too_narrow(self) -> None:
        screen = FakeScreen()

        with mock.patch("curses.has_colors", return_value=False):
            list_top = _draw_header(screen, 45, "", "all", "updated", "filter", None)

        self.assertEqual(list_top, 5)
        self.assertIn("Type to search", screen.line(2))
        self.assertIn("Filter:", screen.line(3))

    def test_tui_search_placeholder_and_active_label_styles(self) -> None:
        with mock.patch("curses.has_colors", return_value=False):
            placeholder = _search_segments("")
            active = _search_segments("api")

        self.assertEqual(placeholder, [("  ", curses.A_DIM), ("Type to search", curses.A_DIM)])
        self.assertEqual("".join(text for text, _attr in active), "  Search: api")
        self.assertNotEqual(active[1][1], curses.A_DIM)


if __name__ == "__main__":
    unittest.main()
