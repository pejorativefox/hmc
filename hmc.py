"""hmc - Python home-manager clone (stdlib only, Python 3.11+)."""
from __future__ import annotations

import argparse
import difflib
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

BACKENDS = {
    # name: (probe binary, base install command)
    "apt": ("apt-get", ["sudo", "apt-get", "install", "-y"]),
    "dnf": ("dnf", ["sudo", "dnf", "install", "-y"]),
    "pacman": ("pacman", ["sudo", "pacman", "-S", "--noconfirm"]),
    "brew": ("brew", ["brew", "install"]),
    "apk": ("apk", ["sudo", "apk", "add"]),
}

HOME = Path.home()


def find_config(cli_path: str | None) -> Path:
    if cli_path:
        return Path(cli_path).expanduser()
    if os.environ.get("HMC_CONFIG"):
        return Path(os.environ["HMC_CONFIG"]).expanduser()
    return Path.home() / ".config" / "hmc" / "home.toml"


def load_config(path: Path) -> tuple[dict, Path]:
    if not path.is_file():
        sys.exit(f"config not found: {path}\ncreate it or pass --config PATH (see home.example.toml)")
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    if not isinstance(cfg, dict):
        sys.exit(f"invalid config {path}: top level must be tables")
    return cfg, path.parent


def home() -> Path:
    return Path(os.environ.get("HOME", str(HOME))).expanduser()


def in_home(dest: Path, hm: Path) -> bool:
    # lexical check (no symlink following): dest itself must live under $HOME.
    # Following symlinks here would misclassify a managed symlink pointing
    # at a repo file outside $HOME as "outside".
    try:
        Path(os.path.abspath(dest)).relative_to(os.path.abspath(hm))
        return True
    except ValueError:
        return False


# --- packages ---

def resolve_package_ops(packages: dict, backend: str | None = None, which=shutil.which):
    """Return [(backend_name, full_cmd_list)]. Skips backends whose binary is missing."""
    if not packages:
        return []
    unknown = set(packages) - set(BACKENDS)
    if unknown:
        sys.exit(f"unknown package backends: {sorted(unknown)} (known: {sorted(BACKENDS)})")
    names = [backend] if backend else list(packages)
    if backend and backend not in packages:
        sys.exit(f"--backend {backend} not present in [packages] (has: {sorted(packages)})")
    ops = []
    for name in names:
        pkgs = packages[name] or []
        if not pkgs:
            continue
        probe, base = BACKENDS[name]
        if not which(probe):
            print(f"packages[{name}]: skip, {probe!r} not found")
            continue
        ops.append((name, base + list(pkgs)))
    if backend and not ops:
        probe = BACKENDS[backend][0]
        sys.exit(f"backend {backend!r} requested but {probe!r} not found on PATH")
    return ops


def apply_packages(packages: dict, dry_run: bool, backend: str | None) -> int:
    ops = resolve_package_ops(packages, backend)
    for name, cmd in ops:
        print(f"+ {' '.join(shlex.quote(c) for c in cmd)}")
        if not dry_run:
            r = subprocess.run(cmd)
            if r.returncode != 0:
                sys.exit(f"package install failed ({name}): exit {r.returncode}")
    return len(ops)


# --- dotfiles ---

def backup_path(dest: Path) -> Path:
    bak = dest.with_name(dest.name + ".bak")
    if not bak.exists() and not bak.is_symlink():
        return bak
    i = 1
    while True:
        cand = dest.with_name(f"{dest.name}.bak.{i}")
        if not cand.exists() and not cand.is_symlink():
            return cand
        i += 1


def apply_dotfiles(dotfiles: dict, cfg_dir: Path, hm: Path, dry_run: bool, show_diff: bool, force: bool) -> int:
    changed = 0
    for dest_rel, spec in (dotfiles or {}).items():
        if not isinstance(spec, dict):
            sys.exit(f"dotfiles[{dest_rel!r}]: must be a table like {{ source = ... }} or {{ text = ... }}")
        has_src, has_text = "source" in spec, "text" in spec
        if has_src == has_text:
            sys.exit(f"dotfiles[{dest_rel!r}]: need exactly one of 'source' or 'text'")
        dest = (hm / dest_rel).expanduser()
        if not force and not in_home(dest, hm):
            sys.exit(f"dotfiles[{dest_rel!r}]: resolves outside $HOME, use --force to allow")
        if has_src:
            src = (cfg_dir / spec["source"]).expanduser().absolute()
            if not src.exists():
                sys.exit(f"dotfiles[{dest_rel!r}]: source not found: {src}")
            if dest.is_symlink():
                try:
                    if (dest.parent / dest.readlink()).resolve() == src.resolve():
                        continue
                except OSError:
                    pass
            changed += 1
            if dry_run:
                print(f"+ link {dest} -> {src}")
                continue
            if dest.exists() or dest.is_symlink():
                bak = backup_path(dest)
                print(f"~ backup {dest} -> {bak}")
                dest.rename(bak)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.symlink_to(src)
            print(f"+ link {dest} -> {src}")
        else:
            want = spec["text"]
            if not isinstance(want, str):
                sys.exit(f"dotfiles[{dest_rel!r}]: 'text' must be a string")
            mode = spec.get("mode")
            cur = dest.read_text() if (dest.is_file() and not dest.is_symlink()) else None
            if cur == want:
                if mode:
                    apply_mode(dest, mode, dry_run)
                continue
            changed += 1
            if dry_run:
                print(f"+ write {dest}")
                if show_diff:
                    print("".join(difflib.unified_diff(
                        (cur or "").splitlines(True), want.splitlines(True),
                        fromfile=f"a/{dest_rel}", tofile=f"b/{dest_rel}")))
                continue
            if dest.is_symlink() or (dest.exists() and cur is not None and cur != want):
                bak = backup_path(dest)
                print(f"~ backup {dest} -> {bak}")
                dest.rename(bak)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(want)
            if mode:
                apply_mode(dest, mode, dry_run)
            print(f"+ write {dest}")
            if show_diff and cur is not None:
                print("".join(difflib.unified_diff(
                    cur.splitlines(True), want.splitlines(True),
                    fromfile=f"a/{dest_rel}", tofile=f"b/{dest_rel}")))
    return changed


def apply_mode(dest: Path, mode: str, dry_run: bool):
    try:
        m = int(mode, 8)
    except ValueError:
        sys.exit(f"{dest}: invalid mode {mode!r}, use e.g. \"0o600\"")
    if dry_run:
        print(f"+ chmod {mode} {dest}")
    else:
        os.chmod(dest, m)


# --- services / env / hooks ---

def apply_services(services: dict, hm: Path, dry_run: bool, show_diff: bool) -> int:
    if not services:
        return 0
    unit_dir = hm / ".config" / "systemd" / "user"
    changed = 0
    for name, spec in services.items():
        if not isinstance(spec, dict) or "text" not in spec:
            sys.exit(f"services[{name!r}]: need {{ text = \"...\" }}")
        dest = unit_dir / name
        want = spec["text"]
        cur = dest.read_text() if dest.is_file() else None
        if cur != want:
            changed += 1
            if dry_run:
                print(f"+ write {dest}")
                if show_diff and cur is not None:
                    print("".join(difflib.unified_diff(
                        cur.splitlines(True), want.splitlines(True),
                        fromfile=f"a/{name}", tofile=f"b/{name}")))
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(want)
                print(f"+ write {dest}")
    systemctl = shutil.which("systemctl")
    wants_enable = [n for n, s in services.items() if s.get("enable", True)]
    wants_start = [n for n, s in services.items() if s.get("start", True)]
    if dry_run:
        if wants_enable or wants_start or changed:
            print("+ systemctl --user daemon-reload" + (
                "" if systemctl else "  (systemctl not found: would skip enable/start)"))
            for n in wants_enable:
                print(f"+ systemctl --user enable {n}")
            for n in wants_start:
                print(f"+ systemctl --user start {n}")
        return changed
    if not systemctl:
        if wants_enable or wants_start:
            print("services: systemctl not found, units written but enable/start skipped (systemd-only v1)")
        return changed
    if changed or wants_enable or wants_start:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    for n in wants_enable:
        r = subprocess.run(["systemctl", "--user", "enable", n])
        if r.returncode != 0:
            sys.exit(f"systemctl enable {n} failed")
    for n in wants_start:
        r = subprocess.run(["systemctl", "--user", "start", n])
        if r.returncode != 0:
            sys.exit(f"systemctl start {n} failed")
    return changed


def apply_env(env: dict, hm: Path, dry_run: bool) -> int:
    if not env:
        return 0
    dest = hm / ".config" / "hmc" / "env.sh"
    lines = ["# generated by hmc - do not edit", ""]
    for k, v in env.items():
        if k == "PATH_prepend":
            lines.append(f'export PATH="{v}:$PATH"')
        elif k == "PATH_append":
            lines.append(f'export PATH="$PATH:{v}"')
        else:
            lines.append(f"export {k}={shlex.quote(str(v))}")
    lines.append("")
    want = "\n".join(lines)
    cur = dest.read_text() if dest.is_file() else None
    if cur == want:
        return 0
    if dry_run:
        print(f"+ write {dest}")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(want)
        print(f"+ write {dest}")
        print("hint: add this to your shell rc once: source ~/.config/hmc/env.sh")
    return 1


def run_hooks(cmds: list[str], cfg_dir: Path, dry_run: bool, label: str):
    for cmd in cmds or []:
        print(f"+ ({label}) {cmd}")
        if not dry_run:
            r = subprocess.run(cmd, shell=True, cwd=cfg_dir)
            if r.returncode != 0:
                sys.exit(f"{label} hook failed: {cmd} (exit {r.returncode})")


def cmd_check(args) -> int:
    cfg_path = find_config(args.config)
    cfg, cfg_dir = load_config(cfg_path)
    hm = home()
    # validate dotfiles
    for dest_rel, spec in (cfg.get("dotfiles") or {}).items():
        if not isinstance(spec, dict) or ("source" in spec) == ("text" in spec):
            sys.exit(f"check: dotfiles[{dest_rel!r}] needs exactly one of source/text")
        if "source" in spec and not (cfg_dir / spec["source"]).exists():
            sys.exit(f"check: dotfiles[{dest_rel!r}] source missing: {cfg_dir / spec['source']}")
        if "mode" in spec:
            int(spec["mode"], 8)  # raises if bad
    # validate packages/services
    pkgs = cfg.get("packages") or {}
    unknown = set(pkgs) - set(BACKENDS)
    if unknown:
        sys.exit(f"check: unknown backends {sorted(unknown)}")
    for name, spec in (cfg.get("services") or {}).items():
        if not isinstance(spec, dict) or "text" not in spec:
            sys.exit(f"check: services[{name!r}] needs text")
    hooks = cfg.get("hooks") or {}
    if set(hooks) - {"pre", "post"}:
        sys.exit(f"check: hooks only supports pre/post, got {sorted(hooks)}")
    print(f"OK: {cfg_path} ({len(cfg.get('dotfiles') or {})} dotfiles, "
          f"{sum(len(v) for v in pkgs.values()) if isinstance(pkgs, dict) else 0} pkgs, "
          f"{len(cfg.get('services') or {})} services) -> $HOME={hm}")
    return 0


def cmd_switch(args) -> int:
    cfg_path = find_config(args.config)
    cfg, cfg_dir = load_config(cfg_path)
    hm = home()
    dry = args.dry_run
    if dry:
        print(f"(dry-run) using {cfg_path} -> {hm}")
    run_hooks((cfg.get("hooks") or {}).get("pre") or [], cfg_dir, dry, "pre")
    n_pkg = apply_packages(cfg.get("packages") or {}, dry, args.backend)
    n_files = apply_dotfiles(cfg.get("dotfiles") or {}, cfg_dir, hm, dry, args.diff, args.force)
    n_svc = apply_services(cfg.get("services") or {}, hm, dry, args.diff)
    n_env = apply_env(cfg.get("env") or {}, hm, dry)
    run_hooks((cfg.get("hooks") or {}).get("post") or [], cfg_dir, dry, "post")
    total = n_pkg + n_files + n_svc + n_env
    print(f"{'would change' if dry else 'changed'}: {total} "
          f"(packages={n_pkg} files={n_files} services={n_svc} env={n_env})")
    if total == 0:
        print("already up to date")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hmc", description="Python home-manager clone (TOML -> $HOME)")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="validate config, no changes")
    c.add_argument("--config", default=None, help="path to home.toml (default: $HMC_CONFIG or ~/.config/hmc/home.toml)")
    c.set_defaults(fn=cmd_check)
    s = sub.add_parser("switch", help="apply config to $HOME")
    s.add_argument("--config", default=None, help="path to home.toml (default: $HMC_CONFIG or ~/.config/hmc/home.toml)")
    s.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    s.add_argument("--diff", action="store_true", help="show unified diffs for text changes")
    s.add_argument("--backend", choices=sorted(BACKENDS), default=None, help="only install this package backend")
    s.add_argument("--force", action="store_true", help="allow dotfile dests outside $HOME")
    s.set_defaults(fn=cmd_switch)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
