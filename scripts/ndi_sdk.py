"""Read the official NDI SDK archive and its installer header."""

from pathlib import Path
import shutil
import tarfile


DEFAULT_NDI_INSTALLER_URL = 'https://downloads.ndi.tv/SDK/NDI_SDK_Linux/Install_NDI_SDK_v6_Linux.tar.gz'
ARCHIVE_MARKER = b'\n__NDI_ARCHIVE_BEGIN__\n'
HEADER_LIMIT = 1024 * 1024


class SdkError(ValueError):
    pass


def extract_installer(package: Path, installer: Path):
    with package.open('rb') as stream:
        is_script = stream.read(2) == b'#!'
    if is_script:
        package.replace(installer)
        return
    try:
        with tarfile.open(package, 'r:gz') as archive:
            scripts = [item for item in archive if item.isfile() and item.name.endswith('.sh')]
            if len(scripts) != 1:
                raise SdkError('El archivo NDI debe contener un único instalador oficial .sh')
            with archive.extractfile(scripts[0]) as stream, installer.open('xb') as target:
                shutil.copyfileobj(stream, target)
    except (tarfile.TarError, EOFError) as exc:
        raise SdkError('No se ha podido leer el paquete oficial de NDI') from exc
    package.unlink()


def installer_header(installer):
    with installer.open('rb') as stream:
        header, marker, _ = stream.read(HEADER_LIMIT).partition(ARCHIVE_MARKER)
    return header, len(header) + len(marker) if marker else None


def read_license(installer):
    header, _ = installer_header(installer)
    _, start, body = header.partition(b'<< NDI_EULA_END\n')
    license_text, end, _ = body.partition(b'\nNDI_EULA_END\n')
    if not start or not end or not license_text.strip():
        raise SdkError('No se ha encontrado el texto de la licencia en el instalador de NDI')
    return license_text


def display_license(text):
    try:
        return text.decode('utf-8')
    except UnicodeDecodeError:
        return text.decode('cp1252', errors='replace')
