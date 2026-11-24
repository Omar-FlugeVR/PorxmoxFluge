#!/usr/bin/env python3
"""Muestra local del panel, con escaneo y publicación de red simulados."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / 'cts/caddy/panel'


def initialize(php, state):
    import sys
    sys.path.insert(0, str(ROOT / 'scripts'))
    from inventory import settings, load_services
    env = {}
    for line in (ROOT / 'config.env.example').read_text(encoding='utf-8').splitlines():
        fields = shlex.split(line, comments=True)
        if fields:
            key, value = fields[0].split('=', 1)
            env[key] = value
    cfg = settings(env)
    spec = importlib.util.spec_from_file_location('panel_seed', ROOT / 'cts/caddy/generate-seed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state.mkdir(parents=True, exist_ok=True)
    (state / 'sessions').mkdir(exist_ok=True)
    config = {'adguardUrl': 'http://127.0.0.1:39999', 'adguardUser': 'demo', 'adguardPass': 'simulated',
              'dashboardDomain': 'inicio.lan', 'serverProxyIp': cfg['CADDY_IP'], 'baseDomain': 'lan',
              'scanAllowedCidrs': ['192.168.1.0/24'],
              'scanPresets': [{'id': 'control', 'label': 'Red de control', 'range': '192.168.1.1-50'},
                              {'id': 'lan', 'label': 'LAN completa', 'range': '192.168.1.0/24'}],
              'dhcpRange': {'start': '192.168.1.100', 'end': '192.168.1.250'}}
    for name, data in [('seed.json', module.render(cfg, load_services(ROOT / 'services.json', cfg))),
                       ('initial-config.json', config),
                       ('initial-auth.json', {'username': 'admin', 'password': 'FlugeDemo2026'})]:
        (state / name).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    subprocess.run([php, str(PANEL / 'bin/initialize.php'), str(state), str(state / 'seed.json'),
                    str(state / 'initial-config.json'), str(state / 'initial-auth.json')], check=True)
    (state / 'initial-auth.json').unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--php', default=shutil.which('php'))
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--state-dir', type=Path, default=ROOT / 'generated/demo')
    args = parser.parse_args()
    php = args.php
    if not php and os.name == 'nt':
        candidate = Path(tempfile.gettempdir()) / 'fluge-dev-tools/php/php.exe'
        if candidate.exists(): php = str(candidate)
    if not php: parser.error('Instala PHP 8.2+ o indica --php /ruta/php.')
    if not 1024 <= args.port <= 65535: parser.error('Usa un puerto entre 1024 y 65535.')
    state = args.state_dir.resolve()
    initialize(php, state)
    env = dict(os.environ, FLUGE_DEMO='1', FLUGE_STATE_DIR=str(state))
    print(f'Muestra local: http://127.0.0.1:{args.port}/', flush=True)
    print('Usuario: admin / Contraseña: FlugeDemo2026', flush=True)
    print('Escaneo, AdGuard, Caddy y monitor NDI simulados. Activa NDI desde Configuración. Ctrl+C para detener.', flush=True)
    command = [php, '-d', f'session.save_path={state / "sessions"}', '-S', f'127.0.0.1:{args.port}',
               '-t', str(PANEL / 'public'), str(PANEL / 'bin/demo-router.php')]
    child = subprocess.Popen(command, env=env)
    try:
        child.wait()
    except KeyboardInterrupt:
        child.terminate()
        child.wait(timeout=10)


if __name__ == '__main__':
    main()
