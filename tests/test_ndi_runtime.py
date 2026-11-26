"""Verify that the exact reviewed SDK supplies only the expected library."""

import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('install_runtime', ROOT / 'cts/caddy/panel/deploy/install-ndi-runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class NdiRuntimeTests(unittest.TestCase):
    def test_extracts_runtime_as_data_after_package_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            payload = io.BytesIO()
            sdk_path = 'NDI SDK for Linux/lib/x86_64-linux-gnu/libndi.so.6.4.0'
            with tarfile.open(fileobj=payload, mode='w:gz') as tar:
                for name, content in [('ignored.sh', b'ignore'), (sdk_path, b'library')]:
                    item = tarfile.TarInfo(name)
                    item.size = len(content)
                    tar.addfile(item, io.BytesIO(content))
            pending = directory / 'pending.sh'
            pending.write_bytes(b'#!/bin/sh\ncat << NDI_EULA_END\nExample licence\nNDI_EULA_END\n'
                                b'exit 99\n__NDI_ARCHIVE_BEGIN__\n' + payload.getvalue())
            destination = directory / 'runtime/lib'
            with (patch.object(runtime, 'STATE', directory), patch.object(runtime, 'RUNTIME', destination),
                  patch.object(runtime, 'can_load', side_effect=lambda path: path.is_file()),
                  patch.object(runtime, 'enable_avahi') as avahi):
                prepared = runtime.perform({'action':'prepare'})
                self.assertEqual(prepared['license'], 'Example licence')
                with self.assertRaises(runtime.SdkError):
                    runtime.perform({'action':'install','package':prepared['package']})
                with self.assertRaises(runtime.SdkError):
                    runtime.perform({'action':'install','package':'a' * 64,'accepted':True})
                self.assertFalse(destination.exists())
                avahi.assert_not_called()
                self.assertEqual(runtime.perform({'action':'install','package':prepared['package'],'accepted':True}), {'installed':True})
                self.assertEqual((destination / runtime.SONAME).read_bytes(), b'library')
                self.assertEqual(len(list(destination.iterdir())), 2)
                self.assertFalse(pending.exists())
                self.assertEqual(runtime.perform({'action':'prepare'}), {'installed':True})
                avahi.assert_called_once_with()
