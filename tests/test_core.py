from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kitty_tabs_recover.names import slugify
from kitty_tabs_recover.session import render_session
from kitty_tabs_recover.snapshot import current_workspace_key, delete_snapshot, load_snapshots, prune_autosaves, rename_snapshot, write_snapshot
from kitty_tabs_recover.tui import _display_rows, _filtered_items


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
        rendered = render_session(sample_snapshot())
        self.assertIn("os_window_title Project", rendered)
        self.assertNotIn("tab_title", rendered)
        self.assertIn("new_tab web", rendered)
        self.assertIn("--env=KTR_WORKSPACE_NAME=Project", rendered)
        self.assertIn("--env=KTR_WORKSPACE_KIND=named", rendered)
        self.assertIn("--env=KTR_WORKSPACE_SLUG=project", rendered)
        self.assertIn("--tab-title=api --title=api --cwd=/tmp/api --var=ktr_focus=1", rendered)
        self.assertIn("focus_tab 1", rendered)

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

    def test_tui_filters_and_dividers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                saved = sample_snapshot("saved")
                write_snapshot(saved)
                auto = sample_snapshot("auto")
                auto["kind"] = "autosave"
                write_snapshot(auto)
                items = load_snapshots(include_autosaves=True)

                self.assertEqual([item.kind for item in _filtered_items(items, "", "saved")], ["named"])
                self.assertEqual([item.kind for item in _filtered_items(items, "", "archived")], ["autosave"])

                rows = _display_rows(items, "all")
                dividers = [row.label for row in rows if row.kind == "divider"]
                self.assertEqual(dividers, ["-- saved workspaces --", "-- autosaved / archived workspaces --"])


if __name__ == "__main__":
    unittest.main()
