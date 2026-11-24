import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("service_apply", ROOT / "scripts/provision/apply-service.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class ServiceApplyTests(unittest.TestCase):
    def test_bad_unit_or_failed_health_restores_files_and_keeps_application_data(self):
        for failure in ("verify", "health"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                stage = root / "stage"
                stage.mkdir()
                existing, added = root / "override.conf", root / "new-config.json"
                database = root / "profile.sqlite"
                existing.write_text("original", encoding="utf-8")
                database.write_bytes(b"buttons-and-connections")
                (stage / "override.conf").write_text("changed", encoding="utf-8")
                (stage / "config.json").write_text('{"port":8000}', encoding="utf-8")
                plan = {"service": "test-service", "verify_unit": str(root / "test.service"),
                        "files": [{"source": "override.conf", "target": str(existing)},
                                  {"source": "config.json", "target": str(added)}],
                        "health": {"kind": "tcp", "host": "127.0.0.1", "port": 5959}}

                def fake_run(*args):
                    if failure == "verify" and args[0] == "systemd-analyze":
                        raise subprocess.CalledProcessError(1, args)

                with patch.object(service, "BACKUP_ROOT", root / "backups"), \
                     patch.object(service, "active", return_value=True), \
                     patch.object(service, "run", side_effect=fake_run), \
                     patch.object(service, "wait_healthy", side_effect=RuntimeError("Puerto cerrado") if failure == "health" else None), \
                     patch.object(service.subprocess, "run"), \
                     patch.object(service.signal, "signal"):
                    with self.assertRaises((subprocess.CalledProcessError, RuntimeError)):
                        service.apply(plan, stage)
                self.assertEqual(existing.read_text(encoding="utf-8"), "original")
                self.assertFalse(added.exists())
                self.assertEqual(database.read_bytes(), b"buttons-and-connections")
                manifest = next((root / "backups").rglob("manifest.json"))
                self.assertEqual(len(json.loads(manifest.read_text(encoding="utf-8"))), 2)

    def test_unchanged_service_does_not_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage = root / "stage"
            stage.mkdir()
            target = root / "config.json"
            target.write_text("same", encoding="utf-8")
            (stage / "config.json").write_text("same", encoding="utf-8")
            plan = {"service": "test-service", "files": [{"source": "config.json", "target": str(target)}],
                    "health": {"kind": "tcp", "host": "127.0.0.1", "port": 5959}}
            with patch.object(service, "active", return_value=True), \
                 patch.object(service, "run") as run, patch.object(service, "wait_healthy"):
                service.apply(plan, stage)
                run.assert_called_once_with("systemctl", "enable", "test-service")


if __name__ == "__main__":
    unittest.main()
