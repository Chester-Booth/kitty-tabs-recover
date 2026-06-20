from __future__ import annotations

import shutil
import subprocess


def _load_gtk():
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        return Gtk
    except Exception:
        return None


def _gtk_choose_close_action(message: str) -> str | None:
    Gtk = _load_gtk()
    if not Gtk:
        return None

    dialog = Gtk.MessageDialog(
        transient_for=None,
        flags=Gtk.DialogFlags.MODAL,
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.NONE,
        text="Close kitty workspace?",
    )
    dialog.set_resizable(False)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Don't save", 2)
    save_button = dialog.add_button("Save As", 1)
    save_button.get_style_context().add_class("suggested-action")
    dialog.set_default_response(1)
    dialog.format_secondary_text(message)

    dialog.show_all()
    response = dialog.run()
    dialog.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()

    if response == 1:
        return "save-as"
    if response == 2:
        return "dont-save"
    return "cancel"


def _gtk_ask_name(default: str) -> tuple[bool, str | None]:
    Gtk = _load_gtk()
    if not Gtk:
        return False, None

    dialog = Gtk.Dialog(title="Save kitty workspace", flags=Gtk.DialogFlags.MODAL)
    dialog.set_resizable(False)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    save_button = dialog.add_button("Save", Gtk.ResponseType.OK)
    save_button.get_style_context().add_class("suggested-action")

    area = dialog.get_content_area()
    area.set_border_width(12)
    area.set_spacing(12)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    heading = Gtk.Label()
    heading.set_markup("<span size='x-large' weight='bold'>Save workspace</span>")
    heading.set_xalign(0)
    label = Gtk.Label(label="Name this Kitty workspace before closing it.")
    label.set_line_wrap(True)
    label.set_xalign(0)
    entry = Gtk.Entry()
    entry.set_text(default)
    body.pack_start(heading, False, False, 0)
    body.pack_start(label, False, False, 0)
    body.pack_start(entry, False, False, 0)
    area.pack_start(body, True, True, 0)

    dialog.set_default_response(Gtk.ResponseType.OK)
    entry.set_activates_default(True)
    dialog.show_all()
    entry.grab_focus()
    response = dialog.run()
    value = entry.get_text().strip()
    dialog.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()

    if response == Gtk.ResponseType.OK and value:
        return True, value
    return True, None


def choose_close_action(message: str) -> str:
    gtk_result = _gtk_choose_close_action(message)
    if gtk_result:
        return gtk_result

    if shutil.which("zenity"):
        result = subprocess.run(
            [
                "zenity",
                "--question",
                "--title=Close kitty workspace?",
                f"--text={message}",
                "--extra-button=Save As",
                "--ok-label=Don't save",
                "--cancel-label=Cancel",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return "dont-save"
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
                "Don't save",
                "--no-label",
                "Save As",
                "--cancel-label",
                "Cancel",
            ],
            check=False,
        )
        if result.returncode == 0:
            return "dont-save"
        if result.returncode == 1:
            return "save-as"
        return "cancel"

    print(message)
    answer = input("[d]on't save, [s]ave as, [Enter] cancel: ").strip().lower()
    if answer in {"d", "dont-save", "don't save", "do not save"}:
        return "dont-save"
    if answer in {"s", "save", "save-as"}:
        return "save-as"
    return "cancel"


def ask_name(default: str) -> str | None:
    gtk_available, gtk_result = _gtk_ask_name(default)
    if gtk_available:
        return gtk_result

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
