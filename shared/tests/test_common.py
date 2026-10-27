"""Tests for genesis_common. Run from the project root:
    python -m unittest discover -s "shared/tests" -v
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genesis_common import (  # noqa: E402
    ConfigError,
    SecretError,
    find_root,
    load_config,
    read_secret,
    read_secret_env,
    redact,
    setup_logging,
)

BASE_TOML = """
[project]
name = "t"
[paths]
secrets = 'secrets'
logs = 'var\\logs'
media = 'E:\\data\\media'
[logging]
level = "INFO"
console = false
"""


class FakeRoot:
    """A throwaway project root (path with a space, like the real one)."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "fake root"
        (root / ".genesis").mkdir(parents=True)
        (root / "secrets").mkdir()
        (root / "apps" / "w9-test").mkdir(parents=True)
        (root / "config.toml").write_text(BASE_TOML, encoding="utf-8")
        return root

    def __exit__(self, *exc) -> None:
        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()
        self._tmp.cleanup()


class ConfigTests(unittest.TestCase):
    def test_load_merge_and_app(self):
        with FakeRoot() as root:
            (root / "config.local.toml").write_text('[logging]\nlevel = "DEBUG"\n', encoding="utf-8")
            (root / "apps" / "w9-test" / "app.toml").write_text("max_mb = 50\n", encoding="utf-8")
            cfg = load_config("w9-test", root=root)
            self.assertEqual(cfg["logging"]["level"], "DEBUG")
            self.assertEqual(cfg["project"]["name"], "t")
            self.assertEqual(cfg["app"]["max_mb"], 50)
            self.assertEqual(cfg.path("paths.logs"), root / "var" / "logs")
            self.assertEqual(str(cfg.path("paths.media")), "E:\\data\\media")
            with self.assertRaises(ConfigError):
                cfg.require("paths.nope")

    def test_find_root_from_subfolder(self):
        with FakeRoot() as root:
            old = os.environ.pop("GENESIS_ROOT", None)
            try:
                self.assertEqual(find_root(root / "apps" / "w9-test").resolve(), root.resolve())
            finally:
                if old is not None:
                    os.environ["GENESIS_ROOT"] = old

    def test_real_project_config_parses(self):
        cfg = load_config("w0-core", root=Path(__file__).resolve().parents[2])
        self.assertEqual(cfg["gpu"]["mode"], "coresident")
        self.assertEqual(str(cfg.path("paths.data_root")), "E:\\data")


class SecretTests(unittest.TestCase):
    def test_read_and_env_file(self):
        with FakeRoot() as root:
            cfg = load_config(root=root)
            (root / "secrets" / "x.txt").write_text("  fake-value-123\n", encoding="utf-8")
            (root / "secrets" / "db.env").write_text('# c\nUSER=a\nPASS="p@ss w0rd"\n', encoding="utf-8")
            self.assertEqual(read_secret("x.txt", cfg), "fake-value-123")
            self.assertEqual(read_secret_env("db.env", cfg), {"USER": "a", "PASS": "p@ss w0rd"})

    def test_env_override_and_bad_names(self):
        with FakeRoot() as root:
            cfg = load_config(root=root)
            os.environ["GENESIS_SECRET_FROM_ENV_TXT"] = "env-value-999"
            try:
                self.assertEqual(read_secret("from-env.txt", cfg), "env-value-999")
            finally:
                del os.environ["GENESIS_SECRET_FROM_ENV_TXT"]
            for bad in ("../config.toml", "a/b", "..", ""):
                with self.assertRaises(SecretError):
                    read_secret(bad, cfg)
            with self.assertRaises(SecretError) as ctx:
                read_secret("missing.txt", cfg)
            self.assertIn("missing.txt", str(ctx.exception))


class LoggingTests(unittest.TestCase):
    def test_json_log_is_redacted(self):
        with FakeRoot() as root:
            cfg = load_config("w9-test", root=root)
            (root / "secrets" / "k.txt").write_text("registered-secret-777", encoding="utf-8")
            secret = read_secret("k.txt", cfg)
            log = setup_logging("w9-test", cfg)
            log.info("value %s", secret, extra={"job_id": 7})
            log.info("cam rtsp://admin:Hunter2@10.0.0.5:554/x token=abc123 bot 123456789:%s", "A" * 35)
            for h in logging.getLogger().handlers:
                h.flush()
            text = (root / "var" / "logs" / "w9-test" / "w9-test.jsonl").read_text(encoding="utf-8")
            lines = [json.loads(line) for line in text.splitlines()]
            self.assertEqual(lines[0]["app"], "w9-test")
            self.assertEqual(lines[0]["job_id"], 7)
            for leaked in ("registered-secret-777", "Hunter2", "abc123", "A" * 35):
                self.assertNotIn(leaked, text)
            self.assertIn("rtsp://***@10.0.0.5", text)

    def test_redact_plain(self):
        self.assertEqual(redact("password=qwerty1 ok"), "password=*** ok")


if __name__ == "__main__":
    unittest.main()
