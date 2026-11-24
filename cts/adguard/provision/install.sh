#!/usr/bin/env bash
# Instalación nativa dentro del CT NUEVO de AdGuard, mediante pct exec.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
timezone="$1"
upstreams="$2"
stage="$(mktemp -d)"
trap 'rm -rf -- "$stage"; rm -f -- /root/.studio-adguard-admin.json' EXIT
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl python3 python3-yaml tzdata
ln -snf "/usr/share/zoneinfo/$timezone" /etc/localtime
printf '%s\n' "$timezone" > /etc/timezone
[[ ! -e /opt/AdGuardHome ]] || { printf 'AdGuard ya existe; no se reinstala.\n' >&2; exit 1; }
curl --fail --silent --show-error --location --retry 3 \
    https://raw.githubusercontent.com/AdguardTeam/AdGuardHome/master/scripts/install.sh \
    --output "$stage/install.sh"
sh "$stage/install.sh" -v

# Usar el asistente oficial local genera el YAML con el esquema de la versión
# instalada, en vez de fijar a mano un schema_version que caducaría.
python3 - "$upstreams" <<'PY'
import base64
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import yaml

for _ in range(60):
    try:
        with urllib.request.urlopen('http://127.0.0.1:3000/control/install/get_addresses', timeout=2) as response:
            if response.status == 200:
                break
    except (OSError, urllib.error.URLError):
        time.sleep(1)
else:
    sys.exit('El asistente local de AdGuard no ha arrancado. Revisa journalctl -u AdGuardHome.')
credentials = json.loads(Path('/root/.studio-adguard-admin.json').read_text())
payload = {
    'web': {'ip': '0.0.0.0', 'port': 80},
    'dns': {'ip': '0.0.0.0', 'port': 53, 'autofix': False},
    'username': credentials['username'], 'password': credentials['password'],
}
request = urllib.request.Request('http://127.0.0.1:3000/control/install/configure',
    data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(request, timeout=30) as response:
    response.read()
auth = base64.b64encode(f"{credentials['username']}:{credentials['password']}".encode()).decode()
for _ in range(30):
    try:
        request = urllib.request.Request('http://127.0.0.1/control/status', headers={'Authorization': f'Basic {auth}'})
        with urllib.request.urlopen(request, timeout=2) as response:
            response.read()
            break
    except (OSError, urllib.error.URLError):
        time.sleep(1)
else:
    sys.exit('AdGuard no responde en el puerto 80 después de configurar el asistente.')
subprocess.run(['systemctl', 'stop', 'AdGuardHome'], check=True)
path = Path('/opt/AdGuardHome/AdGuardHome.yaml')
cfg = yaml.safe_load(path.read_text())
cfg['dns']['upstream_dns'] = sys.argv[1].split()
cfg['dns']['bootstrap_dns'] = sys.argv[1].split()
path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
path.chmod(0o600)
subprocess.run(['/opt/AdGuardHome/AdGuardHome', '--check-config', '-c', str(path), '-w', '/opt/AdGuardHome'], check=True)
subprocess.run(['systemctl', 'enable', '--now', 'AdGuardHome'], check=True)
PY
