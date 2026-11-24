import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("adguard_apply", ROOT / "cts/adguard/provision/apply.py")
adguard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adguard)


class AdGuardTests(unittest.TestCase):
    def test_domain_changes_and_deleted_services_preserve_manual_settings(self):
        original = {
            "users": [{"name": "operador", "password": "$2a$hash"}],
            "dns": {"upstream_dns": ["9.9.9.9"], "port": 53},
            "filtering": {"protection_enabled": True, "rewrites": [
                {"domain": "*.lan", "answer": "192.168.1.12"},
                {"domain": "ndi.lan", "answer": "192.168.1.16"},
                {"domain": "manual.example", "answer": "192.168.1.20", "enabled": False},
            ]},
        }
        previous = original["filtering"]["rewrites"][:2]
        desired = [{"domain": "*.estudio.lan", "answer": "10.23.0.12"}]
        updated = adguard.merge_rewrites(copy.deepcopy(original), previous, desired)
        self.assertEqual(updated["users"], original["users"])
        self.assertEqual(updated["dns"], original["dns"])
        self.assertTrue(updated["filtering"]["protection_enabled"])
        self.assertEqual(updated["filtering"]["rewrites"], [original["filtering"]["rewrites"][-1], *desired])

    def test_first_sync_replaces_conflicting_managed_domains_only(self):
        cfg = {"filtering": {"rewrites": [
            {"domain": "NDI.LAN.", "answer": "192.168.1.99"},
            {"domain": "cam.lan", "answer": "192.168.1.25"},
        ]}}
        desired = [{"domain": "ndi.lan", "answer": "192.168.1.16"}]
        adguard.merge_rewrites(cfg, [], desired)
        self.assertEqual(cfg["filtering"]["rewrites"], [{"domain": "cam.lan", "answer": "192.168.1.25"}, *desired])

    def test_validation_or_restart_failure_rolls_back_config_and_manifest(self):
        for failure in ("validate", "start"):
            for prior_state in (False, True):
                with self.subTest(failure=failure, prior_state=prior_state), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    config = root / "AdGuardHome.yaml"
                    state = root / "state.json"
                    content = yaml.safe_dump({"users": [{"name": "admin", "password": "hash"}],
                                              "filtering": {"rewrites": [{"domain": "old.lan", "answer": "192.168.1.5"}]}})
                    config.write_text(content, encoding="utf-8")
                    state_text = '[{"domain":"old.lan","answer":"192.168.1.5"}]'
                    if prior_state:
                        state.write_text(state_text, encoding="utf-8")

                    def fake_run(*args):
                        if (failure == "validate" and "--check-config" in args) or (failure == "start" and args[:2] == ("systemctl", "start")):
                            raise subprocess.CalledProcessError(1, args)

                    with patch.multiple(adguard, CONFIG=config, STATE=state, BACKUP_ROOT=root / "backups"), \
                         patch.object(adguard, "active", return_value=True), \
                         patch.object(adguard, "run", side_effect=fake_run), \
                         patch.object(adguard, "wait_active"), \
                         patch.object(adguard.subprocess, "run"), \
                         patch.object(adguard.signal, "signal"):
                        with self.assertRaises(subprocess.CalledProcessError):
                            adguard.apply({"rewrites": [{"domain": "*.lan", "answer": "192.168.1.12"}]})
                    self.assertEqual(config.read_text(encoding="utf-8"), content)
                    self.assertEqual(state.exists(), prior_state)
                    if prior_state:
                        self.assertEqual(state.read_text(encoding="utf-8"), state_text)

    def test_repeated_sync_does_not_restart_dns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            desired = [{"domain": "*.lan", "answer": "192.168.1.12", "enabled": True}]
            config, state = root / "config.yaml", root / "state.json"
            config.write_text(yaml.safe_dump({"users": [{"name": "admin"}], "filtering": {"rewrites": desired}}), encoding="utf-8")
            state.write_text(json.dumps(desired), encoding="utf-8")
            with patch.multiple(adguard, CONFIG=config, STATE=state), \
                 patch.object(adguard, "active", return_value=True), \
                 patch.object(adguard, "run") as run:
                adguard.apply({"rewrites": desired})
                run.assert_not_called()

    def test_disabled_rewrites_are_reported_instead_of_publishing_broken_dns(self):
        with self.assertRaisesRegex(ValueError, "desactivadas"):
            adguard.merge_rewrites({"filtering": {"rewrites_enabled": False}}, [],
                                   [{"domain": "*.lan", "answer": "192.168.1.12", "enabled": True}])


if __name__ == "__main__":
    unittest.main()
