"""Minimal checks for hmc (stdlib unittest, pytest-compatible)."""
import os
import tempfile
import unittest
from pathlib import Path

import hmc


def make_cfg_dir(files: dict[str, str]) -> Path:
    d = Path(tempfile.mkdtemp())
    for rel, content in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return d


class TestHmc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old_home
        self.tmp.cleanup()

    def test_text_write_and_idempotent(self):
        cfg_dir = make_cfg_dir({})
        n = hmc.apply_dotfiles(
            {".config/nvim/init.lua": {"text": "x=1\n"}}, cfg_dir, self.home,
            dry_run=False, show_diff=False, force=False)
        self.assertEqual(n, 1)
        self.assertEqual((self.home / ".config/nvim/init.lua").read_text(), "x=1\n")
        n2 = hmc.apply_dotfiles(
            {".config/nvim/init.lua": {"text": "x=1\n"}}, cfg_dir, self.home,
            dry_run=False, show_diff=False, force=False)
        self.assertEqual(n2, 0)

    def test_source_symlink_with_backup(self):
        cfg_dir = make_cfg_dir({"dotfiles/bashrc": "export X=1\n"})
        dest = self.home / ".bashrc"
        dest.write_text("old\n")
        n = hmc.apply_dotfiles(
            {".bashrc": {"source": "dotfiles/bashrc"}}, cfg_dir, self.home,
            dry_run=False, show_diff=False, force=False)
        self.assertEqual(n, 1)
        self.assertTrue(dest.is_symlink())
        self.assertEqual((self.home / ".bashrc.bak").read_text(), "old\n")

    def test_dry_run_writes_nothing(self):
        cfg_dir = make_cfg_dir({})
        n = hmc.apply_dotfiles(
            {".bashrc": {"text": "new\n"}}, cfg_dir, self.home,
            dry_run=True, show_diff=False, force=False)
        self.assertEqual(n, 1)
        self.assertFalse((self.home / ".bashrc").exists())

    def test_package_ops_skip_missing_binary(self):
        ops = hmc.resolve_package_ops({"apt": ["git"]}, which=lambda b: None)
        self.assertEqual(ops, [])
        ops = hmc.resolve_package_ops(
            {"apt": ["git"]}, which=lambda b: "/usr/bin/apt-get" if b == "apt-get" else None)
        self.assertEqual(len(ops), 1)
        self.assertIn("git", ops[0][1])

    def test_env_file(self):
        n = hmc.apply_env({"EDITOR": "nvim"}, self.home, dry_run=False)
        self.assertEqual(n, 1)
        content = (self.home / ".config/hmc/env.sh").read_text()
        self.assertIn('export EDITOR=nvim', content)

    def test_env_path_tilde_expands(self):
        n = hmc.apply_env({"PATH_prepend": "~/.local/bin"}, self.home, dry_run=False)
        self.assertEqual(n, 1)
        content = (self.home / ".config/hmc/env.sh").read_text()
        self.assertIn(str(self.home / ".local/bin"), content)
        self.assertNotIn('"~/', content)
        self.assertNotIn('"~:', content)

    def test_env_path_list(self):
        n = hmc.apply_env({"PATH_append": ["~/.local/bin", "/opt/bin"]}, self.home, dry_run=False)
        self.assertEqual(n, 1)
        content = (self.home / ".config/hmc/env.sh").read_text()
        self.assertIn(str(self.home / ".local/bin") + ":/opt/bin", content)

    def test_mode_only_change_detected(self):
        import os as _os
        cfg_dir = make_cfg_dir({})
        dest_rel = ".config/app.conf"
        n = hmc.apply_dotfiles(
            {dest_rel: {"text": "x=1\n", "mode": "0o600"}}, cfg_dir, self.home,
            dry_run=False, show_diff=False, force=False)
        self.assertEqual(n, 1)
        dest = self.home / dest_rel
        _os.chmod(dest, 0o644)
        n2 = hmc.apply_dotfiles(
            {dest_rel: {"text": "x=1\n", "mode": "0o600"}}, cfg_dir, self.home,
            dry_run=False, show_diff=False, force=False)
        self.assertEqual(n2, 1)
        self.assertEqual(_os.stat(dest).st_mode & 0o777, 0o600)
        n3 = hmc.apply_dotfiles(
            {dest_rel: {"text": "x=1\n", "mode": "0o600"}}, cfg_dir, self.home,
            dry_run=False, show_diff=False, force=False)
        self.assertEqual(n3, 0)

    def test_services_idempotent_when_enabled(self):
        import unittest.mock as mock
        svc = {"hello.timer": {"text": "[Unit]\n", "enable": True, "start": True}}
        with mock.patch.object(hmc.shutil, "which", return_value="/usr/bin/systemctl"):
            with mock.patch.object(hmc.subprocess, "run") as run:
                # is-enabled -> 0, is-active -> 0, so no enable/start calls
                run.side_effect = [
                    mock.Mock(returncode=0),  # is-enabled
                    mock.Mock(returncode=0),  # is-active
                ]
                n = hmc.apply_services(svc, self.home, dry_run=True, show_diff=False)
                self.assertEqual(n, 1)  # 1 file write pending, 0 systemctl actions
                self.assertEqual(run.call_count, 2)
            # second: file already written, probes say enabled -> total 0, no reload
            (self.home / ".config/systemd/user/hello.timer").parent.mkdir(parents=True, exist_ok=True)
            (self.home / ".config/systemd/user/hello.timer").write_text("[Unit]\n")
            with mock.patch.object(hmc.subprocess, "run") as run2:
                run2.side_effect = [
                    mock.Mock(returncode=0),
                    mock.Mock(returncode=0),
                ]
                n2 = hmc.apply_services(svc, self.home, dry_run=True, show_diff=False)
                self.assertEqual(n2, 0)
                self.assertEqual(run2.call_count, 2)

    def test_check_bad_mode_clean_error(self):
        import argparse
        cfg_dir = make_cfg_dir({})
        cfg_path = cfg_dir / "home.toml"
        cfg_path.write_text('[dotfiles]\n".x" = { text = "hi", mode = "nope" }\n')
        with self.assertRaises(SystemExit) as cm:
            hmc.cmd_check(argparse.Namespace(config=str(cfg_path)))
        self.assertIn("invalid mode", str(cm.exception))

    def test_check_rejects_bad_packages(self):
        import argparse
        cfg_dir = make_cfg_dir({})
        cfg_path = cfg_dir / "home.toml"
        cfg_path.write_text('[packages]\napt = "not-a-list"\n')
        with self.assertRaises(SystemExit) as cm:
            hmc.cmd_check(argparse.Namespace(config=str(cfg_path)))
        self.assertIn("packages", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
