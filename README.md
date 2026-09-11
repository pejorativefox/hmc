# hmc — home-manager in Python

Declarative `$HOME` from one TOML file. Stdlib only, Python 3.11+.

Manages: **dotfiles** (symlink or inline text) + **packages** (apt/dnf/pacman/brew/apk) + **systemd user services** + **env file** + **pre/post hooks**. In-place apply with `.bak` backups, `--dry-run`/`--diff` for safety. No generations/rollback yet (planned).

## Install

```bash
pipx install git+ssh://git@github.com/pejorativefox/hmc.git
# or from a clone:
pipx install .
# or zero-install:
python hmc.py --help
```

Requires Python 3.11+ (for `tomllib`). No other dependencies.

## Quick start

```bash
mkdir -p ~/.config/hmc
cp home.example.toml ~/.config/hmc/home.toml
hmc check
hmc switch --dry-run --diff
hmc switch
```

Config lookup: `--config PATH`, else `$HMC_CONFIG`, else `~/.config/hmc/home.toml`.

After first `switch`, add this to your shell rc once:

```bash
source ~/.config/hmc/env.sh
```

## Usage

```bash
hmc check [--config PATH]                       # validate, change nothing
hmc switch [--config PATH] [--dry-run] [--diff] [--backend apt|dnf|pacman|brew|apk] [--force]
```

- `--dry-run`: print what would change, write nothing.
- `--diff`: show unified diffs for text writes (use with `switch --dry-run` to preview).
- `--backend NAME`: only install that `[packages]` backend (default: install every backend whose binary exists).
- `--force`: allow dotfile dests outside `$HOME` (default: refuse).

Apply order (idempotent — re-running a clean tree prints `already up to date`):

`hooks.pre → packages → dotfiles → services → env → hooks.post`

Existing files are backed up to `*.bak` (or `*.bak.N`) before symlink/replace.

## Example (`home.toml`)

```toml
[packages]
apt = ["git", "ripgrep", "neovim"]
brew = ["git", "ripgrep", "neovim"]

[dotfiles]
".bashrc" = { source = "dotfiles/bashrc" }   # relative to home.toml dir, symlinked to ~/.bashrc
".gitconfig" = { source = "dotfiles/gitconfig" }
".config/nvim/init.lua" = { text = "vim.o.number = true\n" }
".ssh/config" = { text = "Host *\n  ServerAliveInterval 60\n", mode = "0o600" }

[services]
"hello.timer" = { text = "[Unit]\nDescription=hello\n\n[Timer]\nOnCalendar=daily\n\n[Install]\nWantedBy=timers.target\n", enable = true, start = true }

[env]
EDITOR = "nvim"
PATH_prepend = "~/.local/bin"   # also supports PATH_append

[hooks]
pre = ["echo applying..."]
post = []
```

### Sections

- **[packages]**: `backend = [pkgs]`. Each backend installs only if its binary is on `PATH` (`apt-get`, `dnf`, `pacman`, `brew`, `apk`), so one file works across machines. Commands: `sudo apt-get install -y …`, `sudo dnf install -y …`, `sudo pacman -S --noconfirm …`, `brew install …`, `sudo apk add …`. No version pinning in v1.
- **[dotfiles]**: key = path under `$HOME`, value = `{ source = "rel/path" }` (symlink) or `{ text = "…", mode = "0o600" }` (write). Exactly one of `source`/`text`.
- **[services]**: key = unit name, value = `{ text = "…", enable = true, start = true }`. Written to `~/.config/systemd/user/`, then `daemon-reload` + `enable`/`start`. systemd-only in v1; without `systemctl` the files are still written and enable/start is skipped with a warning.
- **[env]**: written to `~/.config/hmc/env.sh` as `export` lines (`PATH_prepend`/`PATH_append` get PATH treatment). Source it from your rc; hmc never edits rc files.
- **[hooks]**: `pre`/`post` string lists run with `sh -c` in the config dir, fail-fast.

## Caveats

- No atomic generations or rollback — backups + `--dry-run` only.
- Services need systemd; no launchd/Windows support.
- No secrets templating — keep secrets out of `text =`.

## Dev

```bash
python -m pytest -q
HOME=$(mktemp -d) python hmc.py switch --config home.example.toml --dry-run --diff
```
