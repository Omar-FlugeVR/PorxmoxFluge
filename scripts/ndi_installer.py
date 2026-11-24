#!/usr/bin/env python3
"""Prepara el SDK oficial de NDI y verifica su licencia antes de crear CTs."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from inventory import ConfigError, DEFAULT_NDI_INSTALLER_URL


ROOT = Path(__file__).resolve().parents[1]


def extract_installer(package, installer):
    with package.open("rb") as stream:
        is_script = stream.read(2) == b"#!"
    if is_script:
        package.replace(installer)
        return
    try:
        with tarfile.open(package, "r:gz") as archive:
            scripts = [member for member in archive if member.isfile() and member.name.endswith(".sh")]
            if len(scripts) != 1:
                raise ConfigError("El archivo NDI debe contener un único instalador oficial .sh")
            with archive.extractfile(scripts[0]) as stream, installer.open("xb") as target:
                shutil.copyfileobj(stream, target)
    except (tarfile.TarError, EOFError) as exc:
        raise ConfigError("No se ha podido leer el paquete oficial de NDI") from exc
    package.unlink()


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
        extract_installer(package, installer)

    with installer.open("rb") as stream:
        header = stream.read(1024 * 1024).split(b"\n__NDI_ARCHIVE_BEGIN__\n", 1)[0]
    _, start, body = header.partition(b"<< NDI_EULA_END\n")
    license_text, end, _ = body.partition(b"\nNDI_EULA_END\n")
    if not start or not end or not license_text.strip():
        raise ConfigError("No se ha encontrado el texto de la licencia en el instalador de NDI")
    digest = hashlib.sha256(license_text).hexdigest()
    if digest == config.get("NDI_LICENSE_SHA256", "").lower():
        return digest
    if not sys.stdin.isatty():
        raise ConfigError("La licencia de NDI es nueva o ha cambiado. Revísala y acéptala desde una terminal interactiva.")
    try:
        text = license_text.decode("utf-8")
    except UnicodeDecodeError:
        text = license_text.decode("cp1252", errors="replace")
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
