# kitty-tabs-recover

Autosave and reopen multi-tab kitty windows.

The default path is deliberately compositor-light:

- `ktr daemon` watches kitty and autosaves any OS window with multiple tabs.
- `reopen` shows a full-screen resume picker with search, arrow-key navigation and Enter selection.
- `reopen project` reopens a named workspace.
- `reopen '*'` reopens every named workspace.

For close confirmation without replacing your global Hyprland close binding, use kitty's own prompt:

```conf
confirm_os_window_close 2
```

For a richer optional Hyprland flow with `Cancel`, `Close` and `Save As`, replace your close bind with:

```conf
bind = $mainMod, Q, exec, ktr killactive
```

`ktr killactive` delegates to `hyprctl dispatch killactive` for non-kitty windows, so other applications still close normally.

## Requirements

Kitty remote control must be enabled. A typical kitty config is:

```conf
allow_remote_control yes
listen_on unix:/tmp/kitty-tabs-recover
confirm_os_window_close 2
```

Kitty appends its process ID to the configured socket path, so `ktr` also auto-discovers `/tmp/kitty-tabs-recover-*` when `KITTY_LISTEN_ON` is not set.

## Commands

```sh
ktr save project
ktr save --all
ktr list
ktr list --autosaves
reopen
reopen project
reopen '*'
ktr rename old-name new-name
ktr delete name
ktr daemon
ktr completions zsh
```

In the `reopen` TUI:

- type to filter
- up/down moves selection
- Tab or Shift+Tab switches focus between the Filter and Sort controls
- left/right changes the focused Filter or Sort control
- Filter switches between all, saved and recovery entries
- Sort switches between updated and created time
- `ctrl+a` expands or collapses the autosave history for the selected window; by default only the latest autosave per window is shown
- Enter reopens the selected workspace
- `ctrl+r` renames the selected workspace; renaming an autosave promotes it to a saved workspace
- Esc or Ctrl+C cancels an in-progress rename
- `ctrl+d` deletes the selected workspace
- Esc or Ctrl+C cancels an in-progress delete confirmation
- `ctrl+g` jumps back to the current workspace when `reopen` is launched from a restored workspace
- Esc or Ctrl+C cancels

In this UI, recovery entries are autosaved recovery snapshots. They are not a separate workspace type: saved workspaces persist until you delete them, while autosaves are temporary recovery entries governed by the autosave retention policy.

From the repository checkout, the wrapper scripts work without installing a wheel:

```sh
./scripts/ktr list
./scripts/reopen
```

To install command shims for your user:

```sh
ln -sf "$PWD/scripts/ktr" ~/.local/bin/ktr
ln -sf "$PWD/scripts/reopen" ~/.local/bin/reopen
```

To install the daemon as a user service:

```sh
mkdir -p ~/.config/systemd/user
ln -sf "$PWD/contrib/systemd/kitty-tabs-recover.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now kitty-tabs-recover.service
```

The supplied service relies on the `/tmp/kitty-tabs-recover-*` socket auto-discovery used by `ktr`.

Snapshots are stored below:

```text
~/.local/share/kitty-tabs-recover/
```

## Limits

This restores tabs, tab order, titles, working directories and saved scrollback text. It does not resurrect live processes. Shell history is preserved by your shell's normal history mechanism.
