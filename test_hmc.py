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


if __name__ == "__main__":
    unittest.main()
