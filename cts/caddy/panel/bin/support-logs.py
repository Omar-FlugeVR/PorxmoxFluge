"""Build an administrator's diagnostic ZIP from an explicit list of sources."""

from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from zipfile import ZIP_DEFLATED, ZipFile


MAX_BYTES = 1024 * 1024
STATE = '/var/lib/fluge-control'
REPORTS = '/etc/fluge-control'
JOURNALS = {
    'proxmox/sistema.log': ('pvedaemon', 'pveproxy', 'pve-cluster'),
    'proxmox/actualizaciones.log': ('studio-infra-updates', 'studio-infra-update-jobs'),
    'caddy/caddy.log': ('caddy',),
    'caddy/php-fpm-journal.log': ('php8.2-fpm',),
    'adguard/adguard-home.log': ('AdGuardHome',),
    'companion/companion.log': ('companion',),
    'ndi/ndi-discovery.log': ('ndi-discovery',),
}
FILES = {
    'caddy/php-fpm.log': '/var/log/php8.2-fpm.log',
    'adguard/consultas-dns.jsonl': '/opt/AdGuardHome/data/querylog.json',
    'panel/ndi-history.json': f'{STATE}/ndi-history.json',
    'panel/last-sync.json': f'{STATE}/last-sync.json',
    'panel/update-job.json': f'{REPORTS}/update-job.json',
}


def write_zip(read_source, *, demo=False):
    buffer = BytesIO()
    summary = [
        'Fluge · Registros de diagnóstico',
        datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'Muestra con datos simulados.' if demo else 'Registros recientes del host y de los CT configurados.',
        'Journal: hasta 500 entradas de las últimas 48 horas por archivo.',
        'Cada fuente tiene un límite de 1 MiB. Los archivos sin datos disponibles se indican abajo.',
        '',
    ]
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        for folder in ('proxmox', 'panel', 'caddy', 'adguard', 'companion', 'ndi'):
            archive.mkdir(folder)
        for name, source in (JOURNALS | FILES).items():
            try:
                data = read_source(name, source)
                if not data:
                    summary.append(f'{name} · Sin entradas')
                    continue
                if len(data) > MAX_BYTES:
                    if name.endswith('.json'):
                        summary.append(f'{name} · Supera el límite y se omite para conservar el JSON válido')
                        continue
                    data = data[-MAX_BYTES:].partition(b'\n')[2]
                    summary.append(f'{name} · Recortado al tramo más reciente')
                if data:
                    archive.writestr(name, data)
            except (OSError, subprocess.SubprocessError, LookupError):
                summary.append(f'{name} · No disponible')
        archive.writestr('resumen.txt', '\n'.join(summary) + '\n')
    return buffer.getvalue()


def demo_source(name, _source):
    if name.endswith('.json'):
        return b'{"demo":true}\n'
    if name.endswith('.jsonl'):
        return b'{"demo":true,"question":"inicio.lan"}\n'
    return f'Fluge demo · Registro simulado de {name}\n'.encode()


def collect(panel_ct):
    # Host imports live in the deployed updater. The local example needs only Python.
    from run_updates import PCT, guest, read_guest_json, verify_target

    request = read_guest_json(panel_ct, f'{STATE}/support-request.json', private=True)
    job_id = request.get('id')
    created = request.get('created')
    if not (isinstance(job_id, str) and re.fullmatch(r'[a-f0-9]{32}', job_id)
            and type(created) is int and 0 <= time.time() - created < 600):
        return
    filename = f'support-logs-{job_id}.zip'
    existing = subprocess.run([PCT, 'exec', panel_ct, '--', 'test', '-f', f'{REPORTS}/{filename}'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    if existing.returncode == 0:
        return

    targets = {'panel': panel_ct, 'caddy': panel_ct}
    for role in ('adguard', 'companion', 'ndi'):
        try:
            targets[role] = verify_target(role, require_service=False)
        except subprocess.SubprocessError:
            pass

    def read_source(name, source):
        folder = name.split('/', 1)[0]
        prefix = [] if folder == 'proxmox' else [PCT, 'exec', targets[folder], '--']
        if isinstance(source, tuple):
            command = ['journalctl', '--since=-48h', '-n', '500', '--no-pager', '-o', 'short-iso']
            for unit in source:
                command.extend(['-u', unit])
        else:
            command = ['head' if name.endswith('.json') else 'tail', '-c', str(MAX_BYTES + 1), source]
        return subprocess.run(prefix + command, check=True, capture_output=True, timeout=8).stdout

    guest(panel_ct, 'sh', '-euc', '''
        temp="$(mktemp /etc/fluge-control/.support-logs.XXXXXXXX)"
        trap 'rm -f -- "$temp"' EXIT
        cat > "$temp"
        chgrp www-data "$temp"
        chmod 0640 "$temp"
        mv -fT -- "$temp" "/etc/fluge-control/$1"
        find /etc/fluge-control -maxdepth 1 -type f -name 'support-logs-*.zip' \
            ! -name "$1" -delete
    ''', 'support-logs', filename, input=write_zip(read_source), timeout=20)


if __name__ == '__main__':
    if sys.argv[1] == '--demo':
        sys.stdout.buffer.write(write_zip(demo_source, demo=True))
    else:
        collect(sys.argv[1])
