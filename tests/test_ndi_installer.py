"""Pruebas de aceptación y preparación de NDI sin ejecutar el SDK."""

from hashlib import sha256
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from inventory import ConfigError
from ndi_installer import prepare_installer


LICENSE = "Estos son los términos de prueba.\nOtra cláusula.".encode("cp1252")


class Terminal(io.StringIO):
    def isatty(self):
        return True


def script(license_text):
    return b"#!/bin/sh\ncat << NDI_EULA_END\n" + license_text + b"\nNDI_EULA_END\nexit 99\n"


class NdiInstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stage = self.root / "stage"
        self.package = self.root / "ndi.sh"
        self.package.write_bytes(script(LICENSE))
        self.config = {"NDI_INSTALLER_FILE": str(self.package)}

    def test_accepts_once_and_keeps_the_same_installer(self):
        output = Terminal()
        with patch("sys.stdin", Terminal("s\n")), patch("sys.stderr", output):
            accepted = prepare_installer(self.stage, self.config)
        self.assertEqual(accepted, sha256(LICENSE).hexdigest())
        self.assertIn(LICENSE.decode("cp1252"), output.getvalue())

        self.package.write_bytes(script(b"Licencia diferente"))
        self.config["NDI_LICENSE_SHA256"] = accepted
        with patch("sys.stdin", io.StringIO()):
            self.assertEqual(prepare_installer(self.stage, self.config), accepted)
        self.assertEqual((self.stage / "vendor-install.sh").read_bytes(), script(LICENSE))

    def test_reuses_the_license_on_a_new_package_with_the_same_text(self):
        installer = script(LICENSE).replace(b"exit 99", b"exit 98")
        self.package = self.root / "ndi.tar.gz"
        with tarfile.open(self.package, "w:gz") as archive:
            member = tarfile.TarInfo("Install_NDI_SDK_v6_Linux.sh")
            member.size = len(installer)
            archive.addfile(member, io.BytesIO(installer))
        self.config = {
            "NDI_INSTALLER_FILE": str(self.package), "NDI_LICENSE_SHA256": sha256(LICENSE).hexdigest(),
            "NDI_INSTALLER_SHA256": sha256(self.package.read_bytes()).hexdigest(),
        }
        with patch("sys.stdin", io.StringIO()):
            self.assertEqual(prepare_installer(self.stage, self.config), self.config["NDI_LICENSE_SHA256"])
        self.assertEqual((self.stage / "vendor-install.sh").read_bytes(), installer)

    def test_first_or_changed_license_fails_without_terminal(self):
        for saved in ("", sha256(b"old license").hexdigest()):
            with self.subTest(saved=saved), patch("sys.stdin", io.StringIO()), \
                    self.assertRaisesRegex(ConfigError, "terminal interactiva"):
                prepare_installer(self.stage, {**self.config, "NDI_LICENSE_SHA256": saved})

    def test_refusal_or_eof_cancels(self):
        for response in ("n\n", "\n", ""):
            with self.subTest(response=response), patch("sys.stdin", Terminal(response)), \
                    patch("sys.stderr", Terminal()), self.assertRaisesRegex(ConfigError, "rechazada"):
                prepare_installer(self.stage, self.config)

    def test_rejects_altered_package_or_unrecognized_license(self):
        with self.assertRaisesRegex(ConfigError, "NDI_INSTALLER_SHA256"):
            prepare_installer(self.stage, {**self.config, "NDI_INSTALLER_SHA256": "0" * 64})
        self.package.write_bytes(b"#!/bin/sh\nexit 99\n")
        with self.assertRaisesRegex(ConfigError, "texto de la licencia"):
            prepare_installer(self.stage, self.config)


if __name__ == "__main__":
    unittest.main()
