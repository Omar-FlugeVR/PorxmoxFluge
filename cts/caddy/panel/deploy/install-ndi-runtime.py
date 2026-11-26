#!/usr/bin/python3 -I
"""Install only the Linux runtime from the official NDI SDK."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
from ndi_sdk import DEFAULT_NDI_INSTALLER_URL, SdkError, display_license, extract_installer, installer_header, read_license


STATE = Path('/var/lib/fluge-ndi-runtime')
RUNTIME = Path('/opt/ndi-runtime/lib')
SONAME = 'libndi.so.6'
SDK_MEMBER = re.compile(r'NDI SDK for Linux/lib/x86_64-linux-gnu/(libndi\.so\.6\.\d+\.\d+)')
VALID_FOR = 3600


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(pending):
    with tempfile.TemporaryDirectory(dir=STATE) as temporary:
        archive = Path(temporary) / 'sdk.tar.gz'
        installer = archive.with_name('installer.sh')
        try:
            subprocess.run(['/usr/bin/curl', '--disable', '--fail', '--silent', '--show-error', '--location',
                            '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '12',
                            '--max-time', '90', '--max-filesize', '256M', '--user-agent', 'Mozilla/5.0',
                            '--output', str(archive), '--', DEFAULT_NDI_INSTALLER_URL],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError as exc:
            raise SdkError('No se pudo descargar el SDK oficial. Comprueba la salida a Internet del CT del panel.') from exc
        extract_installer(archive, installer)
        read_license(installer)
        if installer_header(installer)[1] is None:
            raise SdkError('El instalador oficial no contiene el formato de SDK esperado.')
        installer.replace(pending)


def extract_runtime(installer, directory):
    _, offset = installer_header(installer)
    if offset is None:
        raise SdkError('El instalador oficial no contiene el formato de SDK esperado.')
    runtime = None
    with installer.open('rb') as source:
        source.seek(offset)
        with tarfile.open(fileobj=source, mode='r|gz') as archive:
            for item in archive:
                member = SDK_MEMBER.fullmatch(item.name.removeprefix('./'))
                if member is None:
                    continue
                if runtime or not item.isfile() or not 0 < item.size <= 80 * 1024 * 1024:
                    raise SdkError('El SDK contiene una biblioteca NDI inesperada.')
                runtime = directory / member[1]
                with archive.extractfile(item) as stream, runtime.open('xb') as target:
                    shutil.copyfileobj(stream, target)
    if runtime is None:
        raise SdkError('El SDK no contiene libndi 6 para Linux x86_64.')
    return runtime


def can_load(path):
    if not path.is_file():
        return False
    probe = 'import ctypes, sys; ctypes.CDLL(sys.argv[1]).NDIlib_find_create_v2'
    result = subprocess.run(['/usr/sbin/runuser', '-u', 'www-data', '--', '/usr/bin/python3', '-I', '-c',
                             probe, str(path)], capture_output=True, timeout=10)
    return result.returncode == 0


def enable_avahi():
    try:
        if not Path('/usr/sbin/avahi-daemon').is_file():
            apt = ['/usr/bin/apt-get', '-o', 'Acquire::http::Timeout=15', '-o', 'Acquire::https::Timeout=15',
                   '-o', 'Acquire::Retries=1', '-o', 'DPkg::Lock::Timeout=20']
            subprocess.run([*apt, 'update'], check=True, stdout=subprocess.DEVNULL)
            subprocess.run([*apt, 'install', '-y', '--no-install-recommends', 'avahi-daemon'],
                           check=True, stdout=subprocess.DEVNULL, env=dict(os.environ, DEBIAN_FRONTEND='noninteractive'))
        subprocess.run(['/usr/bin/systemctl', 'enable', '--now', 'avahi-daemon.service'],
                       check=True, stdout=subprocess.DEVNULL)
    except subprocess.CalledProcessError as exc:
        raise SdkError('No se pudo instalar o iniciar Avahi en el CT del panel. Revisa APT y vuelve a intentarlo.') from exc


def publish(installer):
    RUNTIME.mkdir(mode=0o755, parents=True, exist_ok=True)
    # umask 077 keeps the download private. The installed library must be readable by PHP's user.
    RUNTIME.parent.chmod(0o755)
    RUNTIME.chmod(0o755)
    with tempfile.TemporaryDirectory(dir=RUNTIME.parent, prefix='.install-') as temporary:
        stage = Path(temporary)
        stage.chmod(0o755)
        library = extract_runtime(installer, stage)
        library.chmod(0o644)
        if not can_load(library):
            raise SdkError('El CT no pudo cargar libndi con sus dependencias. Revisa el runtime en el README.')
        enable_avahi()
        link = stage / SONAME
        link.symlink_to(library.name)
        library.replace(RUNTIME / library.name)
        link.replace(RUNTIME / SONAME)


def perform(request):
    operation = request.get('action')
    token = request.get('package')
    if operation != 'prepare' and (operation != 'install' or request.get('accepted') is not True
                                   or not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{64}', token)):
        raise SdkError('Confirma la licencia del paquete antes de instalar el runtime.')
    pending = STATE / 'pending.sh'
    if can_load(RUNTIME / SONAME):
        pending.unlink(missing_ok=True)
        return {'installed': True}
    current = pending.is_file() and time.time() - pending.stat().st_mtime < VALID_FOR
    if operation == 'prepare':
        if not current:
            download(pending)
        return {'installed': False, 'package': digest(pending), 'license': display_license(read_license(pending))}
    if not current or digest(pending) != token:
        raise SdkError('La preparación caducó o el paquete cambió. Abre de nuevo la licencia de NDI.')
    publish(pending)
    pending.unlink(missing_ok=True)
    return {'installed': True}


def main():
    import fcntl

    if os.geteuid() != 0:
        raise SdkError('Ejecuta el instalador como root en el CT del panel.')
    os.umask(0o077)
    STATE.mkdir(mode=0o700, exist_ok=True)
    interactive = sys.argv[1:] == ['--interactive'] and sys.stdin.isatty()
    request = {'action': 'prepare'} if interactive else json.loads(sys.stdin.buffer.read(2049))
    if not isinstance(request, dict):
        raise SdkError('Petición de instalación no válida.')
    with (STATE / 'install.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SdkError('Ya hay una instalación o descarga del runtime en curso.') from exc
        result = perform(request)
        if interactive and not result['installed']:
            print(result['license'])
            if input('\n¿Aceptas la licencia para instalar el runtime NDI? [s/N] ').strip().lower() not in {'s', 'sí', 'si', 'y', 'yes'}:
                raise SdkError('Instalación cancelada.')
            result = perform({'action': 'install', 'accepted': True, 'package': result['package']})
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (SdkError, json.JSONDecodeError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        sys.exit(1)
    except (OSError, tarfile.TarError, EOFError, subprocess.SubprocessError) as error:
        print(f'NDI runtime: {error}', file=sys.stderr)
        print(json.dumps({'error': 'No se pudo preparar o instalar el runtime en el CT del panel.'}))
        sys.exit(1)
