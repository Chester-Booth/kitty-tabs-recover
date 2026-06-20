from __future__ import annotations

import shutil
import subprocess


_GTK_CSS = b"""
.ktr-body {
  margin: 24px;
}
.ktr-action-area {
  border-top: 1px solid rgba(255, 255, 255, 0.18);
  margin: 0;
  padding: 0;
}
.ktr-action-area button {
  border-radius: 0;
  border-top: 0;
  border-bottom: 0;
  border-left: 0;
  border-right: 1px solid rgba(255, 255, 255, 0.18);
  box-shadow: none;
  min-height: 44px;
  background-image: none;
}
.ktr-action-area button:hover {
  background-color: rgba(255, 255, 255, 0.14);
}
.ktr-action-area button:active {
  background-color: rgba(255, 255, 255, 0.22);
}
.ktr-action-area button.suggested-action {
  color: #58a6ff;
}
"""


def _load_gtk():
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, Gtk

        provider = Gtk.CssProvider()
        provider.load_from_data(_GTK_CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        return Gtk
    except Exception:
        return None


def _gtk_choose_close_action(message: str) -> str | None:
    Gtk = _load_gtk()
    if not Gtk:
        return None

    dialog = Gtk.Dialog(title="Close kitty workspace?", flags=Gtk.DialogFlags.MODAL)
    dialog.set_resizable(False)
    dialog.set_default_size(430, -1)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Don't save", 2)
    save_button = dialog.add_button("Save As", 1)
    save_button.get_style_context().add_class("suggested-action")

    action_area = dialog.get_action_area()
    action_area.get_style_context().add_class("ktr-action-area")
    action_area.set_layout(Gtk.ButtonBoxStyle.EXPAND)
    action_area.set_homogeneous(True)
    action_area.set_spacing(0)
    action_area.set_border_width(0)
    for button in action_area.get_children():
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_size_request(140, 46)

    area = dialog.get_content_area()
    area.set_spacing(0)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    body.get_style_context().add_class("ktr-body")
    heading = Gtk.Label()
    heading.set_markup("<span size='x-large' weight='bold'>Close kitty workspace?</span>")
    heading.set_xalign(0.5)
    label = Gtk.Label(label=message)
    label.set_line_wrap(True)
    label.set_justify(Gtk.Justification.CENTER)
    body.pack_start(heading, False, False, 0)
    body.pack_start(label, False, False, 0)
    area.pack_start(body, True, True, 0)

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
    dialog.set_default_size(430, -1)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    save_button = dialog.add_button("Save", Gtk.ResponseType.OK)
    save_button.get_style_context().add_class("suggested-action")

    action_area = dialog.get_action_area()
    action_area.get_style_context().add_class("ktr-action-area")
    action_area.set_layout(Gtk.ButtonBoxStyle.EXPAND)
    action_area.set_homogeneous(True)
    action_area.set_spacing(0)
    action_area.set_border_width(0)
    for button in action_area.get_children():
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_size_request(215, 46)

    area = dialog.get_content_area()
    area.set_spacing(0)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    body.get_style_context().add_class("ktr-body")
    heading = Gtk.Label()
    heading.set_markup("<span size='x-large' weight='bold'>Save workspace</span>")
    heading.set_xalign(0.5)
    label = Gtk.Label(label="Name this Kitty workspace before closing it.")
    label.set_line_wrap(True)
    label.set_justify(Gtk.Justification.CENTER)
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
