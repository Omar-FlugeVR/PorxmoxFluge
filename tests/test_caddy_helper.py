"""Root publisher checks on its target POSIX filesystem; Caddy is simulated."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

helper = None
if os.name == 'posix':
    spec = importlib.util.spec_from_file_location('caddy_helper', Path(__file__).resolve().parents[1] / 'cts/caddy/panel/deploy/apply-routes.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)


@unittest.skipUnless(helper, 'Root publisher requires the POSIX filesystem used by the CT')
class CaddyHelperTests(unittest.TestCase):
    def test_candidate_rejects_symlinks_wrong_owner_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'plan.json'
            target.write_bytes(b'{}')
            link = Path(directory) / 'link.json'
            link.symlink_to(target)
            with self.assertRaises(OSError): helper.read_candidate(link, os.getuid())
            with self.assertRaises(ValueError): helper.read_candidate(target, os.getuid() + 1)
            self.assertEqual(helper.read_candidate(target, os.getuid()), b'{}')
            target.write_bytes(b'0' * (helper.MAX_SIZE + 1))
            with self.assertRaises(ValueError): helper.read_candidate(target, os.getuid())

    def test_invalid_configuration_restores_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'routes.caddy'
            target.write_bytes(b'previous')
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 1)
            with self.assertRaises(RuntimeError): helper.publish_routes(b'invalid', target, run)
            self.assertEqual(target.read_bytes(), b'previous')
            self.assertEqual(len(calls), 1)

    def test_reload_failure_reloads_restored_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'routes.caddy'
            target.write_bytes(b'previous')
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                if len(calls) >= 3: self.assertEqual(target.read_bytes(), b'previous')
                return subprocess.CompletedProcess(command, 1 if len(calls) == 2 else 0)
            with self.assertRaises(RuntimeError): helper.publish_routes(b'new', target, run)
            self.assertEqual(target.read_bytes(), b'previous')
            self.assertEqual(len(calls), 4)
            self.assertEqual(calls[-1], ['/usr/bin/systemctl', 'reload', 'caddy'])

    def test_successful_publish_and_repeat_do_not_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'routes.caddy'
            def run(command, **kwargs): return subprocess.CompletedProcess(command, 0)
            self.assertTrue(helper.publish_routes(b'new', target, run))
            self.assertEqual(target.read_bytes(), b'new')
            def forbidden(*args, **kwargs): self.fail('Unchanged routes must not reload Caddy')
            self.assertFalse(helper.publish_routes(b'new', target, forbidden))
