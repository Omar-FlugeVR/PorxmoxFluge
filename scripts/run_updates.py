"""Run one panel update request from the Proxmox host."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from check_updates import accepted_ndi_license, compare_panel, compare_product, github_origin, VERSION
from ndi_release import CACHE, installer_digest, read_cached, sdk_files
from ndi_sdk import installer_header


SNAPSHOT = Path(__file__).resolve().parents[1]
PCT = '/usr/sbin/pct'
ROLE_NAMES = {'panel': 'caddy', 'caddy': 'caddy', 'adguard': 'adguard', 'companion': 'companion', 'ndi': 'ndi'}
READ_VERSION = {
    'caddy': ['caddy', 'version'],
    'adguard': ['/opt/AdGuardHome/AdGuardHome', '--version'],
    'companion': ['cat', '/opt/companion/BUILD'],
    'ndi': ['readlink', '-e', '/opt/ndi-discovery/lib/libndi.so.6'],
}


def execute(*command, capture=False, **kwargs):
    result = subprocess.run(command, check=True, stdout=subprocess.PIPE if capture else None, **kwargs)
    return result.stdout.decode().strip() if capture else None


def guest(ct, *command, **kwargs):
    return execute(PCT, 'exec', ct, '--', *command, **kwargs)


def read_guest_json(ct, path, private=False):
    prefix = ['/usr/sbin/runuser', '-u', 'www-data', '--'] if private else []
    try:
        text = guest(ct, *prefix, 'head', '-c', '4096', path, capture=True, timeout=12, stderr=subprocess.DEVNULL)
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (subprocess.CalledProcessError, ValueError):
        return {}


def job_state(ct, request, state):
    guest(ct, 'sh', '-eu', '-c', '''
        temp="$(mktemp /etc/fluge-control/.update-job.XXXXXXXX)"
        trap 'rm -f -- "$temp"' EXIT
        cat > "$temp"
        chmod 0644 "$temp"
        mv -fT -- "$temp" /etc/fluge-control/update-job.json
    ''', input=json.dumps({**request, 'state': state}).encode(), timeout=15)


def valid_request(request, now):
    role = request.get('role')
    target = request.get('version')
    job_id = request.get('id')
    created = request.get('created')
    return (isinstance(role, str) and role in ROLE_NAMES and isinstance(target, str)
            and isinstance(job_id, str) and re.fullmatch(r'[a-f0-9]{32}', job_id)
            and type(created) is int and 0 <= created <= now
            and re.fullmatch({'panel': r'[a-f0-9]{40}', 'ndi': r'SDK 6\.\d+\.\d+'}.get(role, r'\d+\.\d+\.\d+'), target))


def verify_target(role):
    return execute('bash', '-euc', '''
        source "$1/scripts/common.sh"
        ct_settings "$2"
        [[ -n "$CT_IP" && "$(ct_state "$CT_ID")" == existing && "$(pct status "$CT_ID")" == 'status: running' ]]
        verify_ct "$CT_ID"
        assert_services "$2"
        printf '%s\\n' "$CT_ID"
    ''', 'verify-target', SNAPSHOT, role, capture=True, timeout=30)


def update_panel(checkout, ct, target):
    installed = read_guest_json(ct, '/opt/fluge-control/revision.json')
    available = compare_panel(checkout, installed)
    if available['status'] != 'update' or available['latest'] != target:
        raise ValueError('El commit solicitado ya no está disponible para este panel.')
    git = ('git', '-C', checkout)
    if execute(*git, 'status', '--porcelain', capture=True, timeout=10):
        raise ValueError('El repositorio de Proxmox tiene cambios locales.')
    repo = github_origin(checkout)
    execute(*git, 'fetch', '--no-tags', f'https://github.com/{repo}.git', 'HEAD', timeout=150)
    fetched = execute(*git, 'rev-parse', 'FETCH_HEAD', capture=True, timeout=10)
    if fetched != target:
        raise ValueError('La rama principal cambió desde la última comprobación.')
    execute(*git, 'merge', '--ff-only', target, timeout=45)
    if execute(*git, 'rev-parse', 'HEAD', capture=True, timeout=10) != target:
        raise ValueError('El checkout de Proxmox está por delante del commit seleccionado.')
    execute('bash', checkout / 'sync.sh', '--ct', 'caddy', '--yes', pass_fds=(9,))
    if read_guest_json(ct, '/opt/fluge-control/revision.json').get('commit') != target:
        raise ValueError('La revisión publicada no coincide con la solicitada.')


def update_product(ct, role, version, stage=''):
    host = os.environ[role.upper() + '_IP']
    port = os.environ.get(role.upper() + '_PORT', '53' if role == 'adguard' else '80')
    with (SNAPSHOT / 'scripts/provision/upgrade-software.sh').open('rb') as script:
        guest(ct, 'bash', '-s', '--', role, version, host, port, stage, stdin=script)


def update_ndi(ct, target, request):
    sdk = read_cached()
    installer = CACHE / 'installer.sh'
    if sdk.get('version') != target or installer_digest(installer) != sdk.get('sha256'):
        raise ValueError('El paquete oficial de NDI cambió desde la última comprobación.')
    license_hash = sdk['licenseHash']
    if license_hash != accepted_ndi_license() and license_hash != request.get('licenseHash'):
        raise ValueError('La licencia del SDK de NDI requiere aceptación.')
    current = VERSION.search(guest(ct, *READ_VERSION['ndi'], capture=True, timeout=15))
    if not current or tuple(map(int, current.groups()[:3])) >= tuple(map(int, target.split('.'))):
        raise ValueError('NDI Discovery ya tiene esta versión o una posterior.')
    with tempfile.TemporaryDirectory(prefix='fluge-ndi-upgrade-') as directory:
        local = Path(directory)
        if sdk_files(installer, local) != target:
            raise ValueError('El contenido del SDK no coincide con la versión seleccionada.')
        (local / 'installer-license-header.txt').write_bytes(installer_header(installer)[0])
        stage = guest(ct, 'mktemp', '-d', '/root/fluge-ndi-upgrade.XXXXXXXX', capture=True, timeout=10)
        if not re.fullmatch(r'/root/fluge-ndi-upgrade\.[A-Za-z0-9]{8}', stage):
            raise ValueError('El CT no devolvió un directorio de trabajo válido.')
        try:
            for artifact in local.iterdir():
                execute(PCT, 'push', ct, artifact, f'{stage}/{artifact.name}', '--perms', '0600', timeout=90)
            update_product(ct, 'ndi', target, stage)
        finally:
            guest(ct, 'rm', '-rf', '--', stage, timeout=15)
    (CACHE / 'accepted-license').write_text(license_hash + '\n', encoding='ascii')


def perform(checkout, request):
    role, target = request['role'], request['version']
    ct = verify_target(ROLE_NAMES[role])
    if role == 'panel':
        update_panel(checkout, ct, target)
    elif role == 'ndi':
        update_ndi(ct, target.removeprefix('SDK '), request)
    else:
        raw = guest(ct, *READ_VERSION[role], capture=True, timeout=15)
        report = compare_product(role, 'available', raw)
        if report['status'] != 'update' or report['latest'] != target:
            raise ValueError('La versión solicitada ya no coincide con la publicación oficial.')
        update_product(ct, role, target)


def main():
    checkout, panel_ct = Path(sys.argv[1]), sys.argv[2]
    request = read_guest_json(panel_ct, '/var/lib/fluge-control/update-request.json', private=True)
    if not valid_request(request, time.time()):
        return
    request = {key: request[key] for key in ('id', 'role', 'version', 'created', 'licenseHash') if key in request}
    previous = read_guest_json(panel_ct, '/etc/fluge-control/update-job.json')
    if previous.get('id') == request['id']:
        if previous.get('state') == 'running':
            job_state(panel_ct, request, 'failure')
        return
    if time.time() - request['created'] > 900:
        return
    job_state(panel_ct, request, 'running')
    result = 'success'
    try:
        perform(checkout, request)
    except Exception as error:
        print(f'Falló la actualización de {request["role"]}: {error}', file=sys.stderr)
        result = 'failure'
    try:
        execute('bash', SNAPSHOT / 'updates.sh', '--scheduled', pass_fds=(9,))
    except subprocess.CalledProcessError as error:
        print(f'No se pudo refrescar el informe: {error}', file=sys.stderr)
    job_state(panel_ct, request, result)


if __name__ == '__main__':
    main()
