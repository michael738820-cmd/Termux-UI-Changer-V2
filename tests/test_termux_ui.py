import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import termux_ui


class TermuxUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.prefix = self.root / "prefix"
        self.home.mkdir()
        self.paths = termux_ui.config_paths(self.home, self.prefix)

    def tearDown(self):
        self.temp.cleanup()

    def test_prompt_preserves_unmanaged_ps1_and_replaces_managed_block(self):
        original = 'export PS1="my-existing-prompt"\n'
        self.paths["bashrc"].write_text(original)
        termux_ui.update_prompt("Ava", self.paths["bashrc"])
        first = self.paths["bashrc"].read_text()
        self.assertIn(original.strip(), first)
        self.assertIn("Ava@termux", first)
        termux_ui.update_prompt("Kai", self.paths["bashrc"])
        second = self.paths["bashrc"].read_text()
        self.assertIn(original.strip(), second)
        self.assertIn("Kai@termux", second)
        self.assertNotIn("Ava@termux", second)

    def test_prompt_rejects_control_characters_and_quotes_safely(self):
        for unsafe in ("bad\nname", "$(touch /tmp/pwned)", "bad\\name"):
            with self.assertRaises(ValueError):
                termux_ui.update_prompt(unsafe, self.paths["bashrc"])
        termux_ui.update_prompt("O'Brien", self.paths["bashrc"])
        self.assertIn("O'\\''Brien@termux", self.paths["bashrc"].read_text())

    def test_theme_preserves_unrelated_properties(self):
        self.paths["colors"].parent.mkdir(parents=True)
        self.paths["colors"].write_text("# keep me\nforeground=#ffffff\nextra=value\n")
        theme = termux_ui.apply_color_scheme("2", self.paths["colors"])
        text = self.paths["colors"].read_text()
        self.assertEqual(theme, "Dracula")
        self.assertIn("# keep me", text)
        self.assertIn("extra=value", text)
        self.assertIn("background=#282a36", text)
        self.assertIn("cursor=#bd93f9", text)
        self.assertEqual(text.count("foreground="), 1)

    def test_backup_and_restore_existing_and_missing_files(self):
        self.paths["bashrc"].write_text("original\n")
        backup = termux_ui.create_backup(self.paths, self.home)
        self.paths["bashrc"].write_text("changed\n")
        self.paths["colors"].parent.mkdir(parents=True, exist_ok=True)
        self.paths["colors"].write_text("new file\n")
        restored = termux_ui.restore_backup(backup, self.home, expected_paths=self.paths)
        self.assertIn("bashrc", restored)
        self.assertEqual(self.paths["bashrc"].read_text(), "original\n")
        self.assertFalse(self.paths["colors"].exists())
        self.assertGreaterEqual(len(termux_ui.list_backups(self.home)), 2)

    def test_backup_payload_is_base64_and_tracks_absence(self):
        backup = termux_ui.create_backup(self.paths, self.home)
        data = json.loads(backup.read_text())
        self.assertFalse(data["files"]["bashrc"]["exists"])
        self.assertEqual(data["version"], 1)

    def test_restore_rejects_backup_with_unexpected_target_path(self):
        backup = termux_ui.create_backup(self.paths, self.home)
        data = json.loads(backup.read_text())
        data["files"]["bashrc"]["path"] = str(self.root / "outside.txt")
        backup.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            termux_ui.restore_backup(backup, self.home, expected_paths=self.paths)

    def test_motd_uses_supplied_prefix_and_preserves_unmanaged_text(self):
        self.paths["motd"].parent.mkdir(parents=True)
        self.paths["motd"].write_text("Existing Termux notice\n")
        termux_ui.update_motd("TermuxUser", self.paths["motd"])
        first = self.paths["motd"].read_text()
        self.assertIn("Existing Termux notice", first)
        self.assertIn("Welcome to Termux, TermuxUser!", first)
        termux_ui.update_motd("AnotherUser", self.paths["motd"])
        second = self.paths["motd"].read_text()
        self.assertIn("Existing Termux notice", second)
        self.assertIn("Welcome to Termux, AnotherUser!", second)
        self.assertNotIn("TermuxUser!", second)

    @patch("termux_ui.shutil.which", return_value=None)
    def test_reload_reports_missing_command(self, _which):
        ok, message = termux_ui.reload_settings()
        self.assertFalse(ok)
        self.assertIn("not found", message)

    def test_all_themes_have_three_hex_colors(self):
        self.assertEqual(len(termux_ui.COLOR_SCHEMES), 8)
        for _, bg, fg, cursor in termux_ui.COLOR_SCHEMES.values():
            for value in (bg, fg, cursor):
                self.assertRegex(value, r"^#[0-9a-fA-F]{6}$")

    def test_invalid_theme_rejected(self):
        with self.assertRaises(ValueError):
            termux_ui.apply_color_scheme("99", self.paths["colors"])

    @patch("termux_ui.urllib.request.urlopen")
    def test_update_check_compares_versions_numerically(self, urlopen):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, _limit):
                return b'VERSION = "2.1.0"\n'
        urlopen.return_value = Response()
        latest, message = termux_ui.check_for_updates()
        self.assertEqual(latest, "2.1.0")
        self.assertIn("newer version", message)


if __name__ == "__main__":
    unittest.main()
