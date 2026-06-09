from __future__ import annotations

import shutil
import subprocess


def choose_close_action(message: str) -> str:
    if shutil.which("zenity"):
        result = subprocess.run(
            [
                "zenity",
                "--question",
                "--title=Close kitty workspace?",
                f"--text={message}",
                "--extra-button=Save As",
                "--ok-label=Close",
                "--cancel-label=Cancel",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return "close"
        if "Save As" in result.stdout:
            return "save-as"
        return "cancel"

    if shutil.which("kdialog"):
        result = subprocess.run(
            [
                "kdialog",
                "--title",
                "Close kitty workspace?",
                "--warningyesnocancel",
                message,
                "--yes-label",
                "Close",
                "--no-label",
                "Save As",
                "--cancel-label",
                "Cancel",
            ],
            check=False,
        )
        if result.returncode == 0:
            return "close"
        if result.returncode == 1:
            return "save-as"
        return "cancel"

    print(message)
    answer = input("[c]lose, [s]ave as, [Enter] cancel: ").strip().lower()
    if answer in {"c", "close"}:
        return "close"
    if answer in {"s", "save", "save-as"}:
        return "save-as"
    return "cancel"


def ask_name(default: str) -> str | None:
    if shutil.which("zenity"):
        result = subprocess.run(
            [
                "zenity",
                "--entry",
                "--title=Save kitty workspace",
                "--text=Workspace name",
                f"--entry-text={default}",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None

    if shutil.which("kdialog"):
        result = subprocess.run(
            ["kdialog", "--title", "Save kitty workspace", "--inputbox", "Workspace name", default],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None

    answer = input(f"Workspace name [{default}]: ").strip()
    return answer or default
