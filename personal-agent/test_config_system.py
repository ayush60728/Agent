import actions
"""
Tests for the new config system (Phase 1).
"""
import os
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import config

class TestConfigSystem(unittest.TestCase):
    def setUp(self):
        # We don't want to overwrite the user's real settings.json, so we mock _SETTINGS_FILE
        self.test_dir = Path(__file__).parent / "test_scratch"
        self.test_dir.mkdir(exist_ok=True)
        self.test_settings_file = self.test_dir / "test_settings.json"
        
        # Ensure clean state
        if self.test_settings_file.exists():
            self.test_settings_file.unlink()

        self.patcher = patch("config._SETTINGS_FILE", self.test_settings_file)
        self.patcher.start()
        
        # Reset config to defaults
        config.reload()

    def tearDown(self):
        self.patcher.stop()
        if self.test_settings_file.exists():
            self.test_settings_file.unlink()
        try:
            self.test_dir.rmdir()
        except OSError:
            pass

    def test_default_values(self):
        """When settings.json doesn't exist, we should get defaults."""
        self.assertFalse(self.test_settings_file.exists())
        self.assertEqual(config.get("model.name"), "qwen3-nothink")
        self.assertFalse(config.get("safety.dry_run"))
        # Check module attrs
        self.assertFalse(config.DRY_RUN)
        self.assertEqual(config.VERBOSITY, "terse")

    def test_set_and_get(self):
        """Updating a value updates memory and disk, and module attrs."""
        config.set("safety.dry_run", True)
        self.assertTrue(config.get("safety.dry_run"))
        self.assertTrue(config.DRY_RUN)

        # Check disk
        self.assertTrue(self.test_settings_file.exists())
        with open(self.test_settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertTrue(data["safety"]["dry_run"])

    def test_reload_from_disk(self):
        """Reloading picks up external changes to the file."""
        # Write directly to disk
        data = config.get_settings()
        data["ux"]["verbosity"] = "detailed"
        with open(self.test_settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
            
        # Before reload, memory is still terse
        self.assertEqual(config.VERBOSITY, "terse")
        
        # Reload
        config.reload()
        
        self.assertEqual(config.get("ux.verbosity"), "detailed")
        self.assertEqual(config.VERBOSITY, "detailed")

    def test_deep_merge(self):
        """Only partial overrides from disk are merged with defaults."""
        partial_data = {"voice": {"tts_rate": 200}}
        with open(self.test_settings_file, "w", encoding="utf-8") as f:
            json.dump(partial_data, f)
            
        config.reload()
        
        # Custom value applies
        self.assertEqual(config.get("voice.tts_rate"), 200)
        self.assertEqual(config.TTS_RATE, 200)
        
        # Other values in voice block are untouched
        self.assertEqual(config.get("voice.tts_volume"), 1.0)
        
        # Other blocks are untouched
        self.assertEqual(config.get("model.name"), "qwen3-nothink")

if __name__ == "__main__":
    unittest.main()
