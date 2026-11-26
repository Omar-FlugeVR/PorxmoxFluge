"""Cache and inspect the official Linux SDK for Discovery Server updates."""

from hashlib import file_digest, sha256
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ndi_sdk import DEFAULT_NDI_INSTALLER_URL, SdkError, extract_installer, installer_header, read_license


CACHE = Path('/var/cache/studio-infra/ndi')
VERSIONED_LIBRARY = re.compile(r'libndi\.so\.(6\.\d+\.\d+)')
ARCH = 'x86_64-linux-gnu'
SERVER = f'NDI SDK for Linux/bin/{ARCH}/ndi-discovery-server'
LIBRARIES = f'NDI SDK for Linux/lib/{ARCH}/'


def sdk_files(installer, output=None):
    _, offset = installer_header(installer)
    if offset is None:
        raise SdkError('El SDK de NDI no contiene el archivo esperado.')
    found = {}
    with installer.open('rb') as stream:
        stream.seek(offset)
        with tarfile.open(fileobj=stream, mode='r|gz') as bundle:
            for item in bundle:
                name = item.name.removeprefix('./')
                version = VERSIONED_LIBRARY.fullmatch(name.removeprefix(LIBRARIES)) if name.startswith(LIBRARIES) else None
                role = 'server' if name == SERVER else 'library' if version else None
                if not role:
                    continue
                if role in found or not item.isfile() or not 0 < item.size <= 80 * 1024 * 1024:
                    raise SdkError('El SDK contiene archivos inesperados para NDI en Linux.')
                found[role] = version[1] if version else True
                if output is not None:
                    target = output / ('ndi-discovery-server' if role == 'server' else 'libndi.so.' + version[1])
                    with bundle.extractfile(item) as source, target.open('xb') as destination:
                        shutil.copyfileobj(source, destination)
    if set(found) != {'server', 'library'}:
        raise SdkError('El SDK no incluye el servidor y la biblioteca para Linux x86_64.')
    return found['library']


def read_cached(cache=CACHE):
    try:
        result = json.loads((cache / 'release.json').read_text(encoding='utf-8'))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def installer_digest(installer):
    with installer.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def fetch_release(cache=CACHE):
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = read_cached(cache)
    installed_package = cache / 'installer.sh'
    cached = installed_package.is_file() and installer_digest(installed_package) == metadata.get('sha256')
    headers = {'User-Agent': 'Mozilla/5.0'}
    if cached and metadata.get('etag'):
        headers['If-None-Match'] = metadata['etag']
    elif cached and metadata.get('modified'):
        headers['If-Modified-Since'] = metadata['modified']
    try:
        response = urlopen(Request(DEFAULT_NDI_INSTALLER_URL, headers=headers), timeout=15)
    except HTTPError as error:
        if error.code == 304 and cached:
            return metadata
        raise
    with response, tempfile.TemporaryDirectory(dir=cache) as directory:
        if not response.geturl().startswith('https://'):
            raise SdkError('El paquete oficial de NDI debe descargarse por HTTPS.')
        stage = Path(directory)
        archive = stage / 'download'
        installer = stage / 'installer.sh'
        total = 0
        with archive.open('wb') as out:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 256 * 1024 * 1024:
                    raise SdkError('El SDK supera el tamaño máximo admitido.')
                out.write(chunk)
        extract_installer(archive, installer)
        license_text = read_license(installer)
        metadata = {'version': sdk_files(installer), 'sha256': installer_digest(installer),
                    'licenseHash': sha256(license_text).hexdigest(), 'etag': response.headers.get('ETag'),
                    'modified': response.headers.get('Last-Modified')}
        (stage / 'release.json').write_text(json.dumps(metadata), encoding='utf-8')
        installer.replace(installed_package)
        (stage / 'release.json').replace(cache / 'release.json')
        return metadata
