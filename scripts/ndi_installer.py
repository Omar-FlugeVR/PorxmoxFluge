#!/usr/bin/env python3
"""Prepara el SDK oficial de NDI y verifica su licencia antes de crear CTs."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

from inventory import ConfigError
from ndi_sdk import DEFAULT_NDI_INSTALLER_URL, SdkError, display_license, extract_installer, read_license


ROOT = Path(__file__).resolve().parents[1]


def prepare_installer(stage, config):
    stage.mkdir(mode=0o700, exist_ok=True)
    installer = stage / "vendor-install.sh"
    if not installer.exists():
        package = stage / "installer.download"
        local = config.get("NDI_INSTALLER_FILE")
        if local:
            shutil.copyfile(ROOT / local, package)
        else:
            url = config.get("NDI_INSTALLER_URL", DEFAULT_NDI_INSTALLER_URL)
            if not url.startswith("https://"):
                raise ConfigError("NDI_INSTALLER_URL debe usar HTTPS")
            subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location",
                            "--proto", "=https", "--proto-redir", "=https", "--retry", "3",
                            "--connect-timeout", "15", "--user-agent", "Mozilla/5.0",
                            "--output", str(package), "--", url], check=True)
        expected = config.get("NDI_INSTALLER_SHA256", "").lower()
        if expected:
            with package.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ConfigError("El instalador de NDI no coincide con NDI_INSTALLER_SHA256")
        try:
            extract_installer(package, installer)
        except SdkError as exc:
            raise ConfigError(str(exc)) from exc
    try:
        license_text = read_license(installer)
    except SdkError as exc:
        raise ConfigError(str(exc)) from exc
    digest = hashlib.sha256(license_text).hexdigest()
    if digest == config.get("NDI_LICENSE_SHA256", "").lower():
        return digest
    if not sys.stdin.isatty():
        raise ConfigError("La licencia de NDI es nueva o ha cambiado. Revísala y acéptala desde una terminal interactiva.")
    text = display_license(license_text)
    print(f"\nLicencia del instalador oficial de NDI\n\n{text}\n", file=sys.stderr)
    print("¿Aceptas la licencia de NDI? [s/N] ", end="", file=sys.stderr, flush=True)
    if sys.stdin.readline().strip().casefold() not in {"s", "sí", "si", "y", "yes"}:
        raise ConfigError("Licencia de NDI rechazada. Instalación de CTs cancelada.")
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path)
    args = parser.parse_args()
    try:
        print(prepare_installer(args.stage, os.environ))
    except (ConfigError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
