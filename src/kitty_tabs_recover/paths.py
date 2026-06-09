from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "kitty-tabs-recover"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def workspaces_dir() -> Path:
    return data_dir() / "workspaces"


def autosaves_dir() -> Path:
    return data_dir() / "autosaves"


def ensure_dirs() -> None:
    workspaces_dir().mkdir(parents=True, exist_ok=True)
    autosaves_dir().mkdir(parents=True, exist_ok=True)
