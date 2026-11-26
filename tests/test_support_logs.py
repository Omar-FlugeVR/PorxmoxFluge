from io import BytesIO
import importlib.util
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_updates

spec = importlib.util.spec_from_file_location('support_logs', ROOT / 'cts/caddy/panel/bin/support-logs.py')
support_logs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support_logs)


class SupportLogsTests(unittest.TestCase):
    def test_worker_collects_an_archive_even_with_a_ct_down(self):
        request = {'id': 'a' * 32, 'created': int(time.time())}
        unavailable = subprocess.CalledProcessError(1, 'pct')

        def read(command, **_kwargs):
            if 'test' in command:
                return SimpleNamespace(returncode=1)
            return SimpleNamespace(stdout=b'{"example":true}\n' if 'head' in command else b'Example log\n')

        with (patch.object(run_updates, 'read_guest_json', return_value=request),
              patch.object(run_updates, 'verify_target', side_effect=[unavailable, '112', '113']) as verify,
              patch.object(run_updates, 'guest') as publish,
              patch.object(support_logs.subprocess, 'run', side_effect=read)):
            support_logs.collect('111')
        self.assertEqual([call.args[0] for call in verify.call_args_list], ['adguard', 'companion', 'ndi'])
        self.assertEqual(publish.call_args.args[-1], 'support-logs-' + request['id'] + '.zip')
        with ZipFile(BytesIO(publish.call_args.kwargs['input'])) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn('adguard/', archive.namelist())
            self.assertNotIn('adguard/adguard-home.log', archive.namelist())
            self.assertIn(b'adguard/adguard-home.log', archive.read('resumen.txt'))
            self.assertEqual(archive.read('ndi/ndi-discovery.log'), b'Example log\n')
            self.assertEqual(archive.read('panel/last-sync.json'), b'{"example":true}\n')

    def test_worker_ignores_expired_or_invalid_requests(self):
        for request in ({'id': 'a' * 32, 'created': int(time.time()) - 601},
                        {'id': '../auth.json', 'created': int(time.time())}):
            with (patch.object(run_updates, 'read_guest_json', return_value=request),
                  patch.object(support_logs.subprocess, 'run') as command):
                support_logs.collect('111')
                command.assert_not_called()
