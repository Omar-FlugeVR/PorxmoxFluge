#!/usr/bin/env bash
set -euo pipefail

caddy_settings() {
    # Consumidas por create_ct/verify_ct en scripts/common.sh.
    # shellcheck disable=SC2034
    CT_ID="$CADDY_CT_ID" CT_IP="$CADDY_IP" CT_HOSTNAME="$CADDY_HOSTNAME" CT_CORES=1 CT_RAM=512 CT_DISK=4 CT_ORDER=2
}

caddy_assert_services() {
    pct exec "$CADDY_CT_ID" -- bash -c 'command -v caddy >/dev/null && systemctl cat caddy.service >/dev/null' \
        || die "Caddy no está instalado en CT $CADDY_CT_ID; no se aprovisionan CTs preexistentes."
}

provision_caddy() {
    pct exec "$CADDY_CT_ID" -- bash -s -- "$TIMEZONE" \
        < "$REPO_DIR/cts/caddy/provision/install.sh"
    ok 'Caddy instalado desde el repositorio apt oficial.'
}

sync_panel_ndi() {
    [[ -n "${CADDY_IP:-}" ]] || return 0
    local state ip='' cli=/opt/fluge-control/bin/configure-ndi.php
    # NDI puede instalarse por separado mientras el panel todavía no está disponible.
    (
        [[ "$(ct_state "$CADDY_CT_ID")" == existing && "$(pct status "$CADDY_CT_ID")" == 'status: running' ]] \
            && verify_ct_config "$CADDY_CT_ID" \
            && pct exec "$CADDY_CT_ID" -- test -f "$cli"
    ) >/dev/null 2>&1 || return 0

    if [[ -n "${NDI_IP:-}" ]]; then
        state="$(ct_state "$NDI_CT_ID" 2>/dev/null)" || return 0
        if [[ "$state" == existing ]]; then
            # Un CT detenido mantiene el destino previamente comprobado.
            (
                [[ "$(pct status "$NDI_CT_ID")" == 'status: running' ]] \
                    && verify_ct_config "$NDI_CT_ID" && ndi_has_services
            ) >/dev/null 2>&1 || return 0
            ip="$NDI_IP"
        fi
    fi
    pct exec "$CADDY_CT_ID" -- runuser -u www-data -- php "$cli" /var/lib/fluge-control "$ip" "$NDI_PORT" </dev/null \
        || warn 'No se pudo actualizar el destino automático NDI del panel.'
}

configure_panel_updates() {
    local units="$REPO_DIR/cts/caddy/updates" path snapshot=/etc/studio-infra/updates
    install -d -m 0700 /etc/studio-infra "$snapshot" || return
    if [[ -f /etc/systemd/system/studio-infra-updates.timer ]]; then
        systemctl stop studio-infra-updates.timer studio-infra-updates.service || return
    fi
    # El temporizador ejecuta la copia desplegada. Un git pull solo cambia el checkout.
    for path in updates.sh scripts/{check_updates.py,run_updates.py,panel_release.py,ndi_release.py,ndi_sdk.py,common.sh,ct-network.py,inventory.py,post-install.sh,render.py} \
        scripts/provision/upgrade-software.sh \
        cts/{adguard,caddy,companion,ndi}/host.sh cts/caddy/panel/bin/support-logs.py; do
        install -D -m 0600 -- "$REPO_DIR/$path" "$snapshot/$path" || return
    done
    printf '%s\n' "${FLUGE_UPDATES_CHECKOUT:-$REPO_DIR}" > "$snapshot/checkout" &&
        chmod 0600 "$snapshot/checkout" &&
        install -m 0644 "$units/"studio-infra-{updates,update-jobs}.{service,timer} /etc/systemd/system/ &&
        systemctl daemon-reload && systemctl enable --now studio-infra-updates.timer studio-infra-update-jobs.timer &&
        systemctl start --no-block studio-infra-updates.service
}

apply_caddy() {
    local stage revision='' dirty=false changes minimum
    need_commands tar
    if revision="$(git -C "$REPO_DIR" rev-parse --verify HEAD 2>/dev/null)" && [[ "$revision" =~ ^[a-f0-9]{40}$ ]]; then
        if ! changes="$(git -C "$REPO_DIR" status --porcelain -- cts/caddy/panel panel-version.json)" || [[ -n "$changes" ]]; then dirty=true; fi
    else
        revision=''
    fi
    minimum="$(PYTHONPATH="$REPO_DIR/scripts" python3 - "$REPO_DIR" "$TEMP_DIR/revision.json" "$revision" "$dirty" <<'PY'
import json, os, subprocess, sys
from http.client import HTTPException
from pathlib import Path
from check_updates import deployment_release

checkout, destination, commit, dirty = sys.argv[1:]
try:
    supplied = json.loads(os.environ['FLUGE_PANEL_RELEASE']) if 'FLUGE_PANEL_RELEASE' in os.environ else None
    release = deployment_release(checkout, commit, supplied)
except (OSError, HTTPException, ValueError, subprocess.SubprocessError) as error:
    raise SystemExit(f'No se puede identificar la versión del panel. {error}') from None
Path(destination).write_text(json.dumps({'commit': commit or None, 'dirty': dirty == 'true', 'release': release}), encoding='utf-8')
print(release.get('minimum_version', ''))
PY
)"
    if [[ -n "$minimum" ]]; then
        pct exec "$CADDY_CT_ID" -- sh -ec 'if test -d /opt/fluge-control; then cat /opt/fluge-control/revision.json; else printf null; fi' > "$TEMP_DIR/installed-panel.json" \
            || die 'No se puede verificar la versión instalada del panel.'
        PYTHONPATH="$REPO_DIR/scripts" python3 - "$minimum" "$TEMP_DIR/installed-panel.json" <<'PY'
import json, sys
from pathlib import Path
from panel_release import validate_release, version_parts

minimum, filename = sys.argv[1:]
try:
    revision = json.loads(Path(filename).read_text(encoding='utf-8'))
    if revision is not None:
        release = validate_release(revision.get('release') if isinstance(revision, dict) else None)
        if version_parts(release['version']) < version_parts(minimum):
            raise SystemExit(f'La versión instalada {release["version"]} debe pasar primero por {minimum}. Usa Actualizaciones en el panel.')
except (OSError, ValueError):
    raise SystemExit('No se conoce la versión instalada del panel. Comprueba la revisión antes de aplicar el mínimo.') from None
PY
    fi
    mkdir -p -- "$REPO_DIR/.secrets"
    chmod 700 -- "$REPO_DIR/.secrets"
    PYTHONPATH="$REPO_DIR/scripts" python3 - "$REPO_DIR/.secrets" "$TEMP_DIR/rendered/caddy/panel-config.json" <<'PY'
import ipaddress, json, os, pathlib, secrets, sys
from inventory import role_networks, settings
private, destination = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
auth = private / 'panel-admin.json'
if not auth.exists():
    with os.fdopen(os.open(auth, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
        json.dump({'username': 'admin', 'password': 'Fluge1' + secrets.token_urlsafe(24)}, out)
os.chmod(auth, 0o600)
adguard_file = private / 'adguard-admin.json'
adguard = json.loads(adguard_file.read_text()) if adguard_file.exists() else {}
cfg = settings()
net = ipaddress.ip_network(role_networks(cfg, 'caddy')[0]['ip'], strict=False)
first, last = int(net.network_address) + 1, int(net.broadcast_address) - 1
config = {'baseDomain': os.environ['BASE_DOMAIN'],
          'dashboardDomain': os.environ['LANDING_SUBDOMAIN'] + '.' + os.environ['BASE_DOMAIN'],
          'serverProxyIp': cfg['CADDY_IP'], 'adguardUrl': 'http://' + cfg['ADGUARD_IP'] if cfg['ADGUARD_IP'] else '',
          'adguardUser': adguard.get('username', ''), 'adguardPass': adguard.get('password', ''),
          'scanAllowedCidrs': [str(net)], 'scanPresets': [],
          'dhcpRange': {'start': str(ipaddress.ip_address(min(last, first + 99))),
                        'end': str(ipaddress.ip_address(min(last, first + 249)))}}
destination.write_text(json.dumps(config), encoding='utf-8')
os.chmod(destination, 0o600)
PY
    tar -C "$REPO_DIR/cts/caddy/panel" --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='./bin/demo-router.php' -cf "$TEMP_DIR/panel.tar" .
    stage="$(pct exec "$CADDY_CT_ID" -- mktemp -d /tmp/studio-caddy.XXXXXXXX)"
    pct push "$CADDY_CT_ID" "$TEMP_DIR/rendered/caddy/Caddyfile" "$stage/Caddyfile" --perms 0600
    pct push "$CADDY_CT_ID" "$TEMP_DIR/rendered/caddy/seed.json" "$stage/seed.json" --perms 0600
    pct push "$CADDY_CT_ID" "$TEMP_DIR/rendered/caddy/panel-config.json" "$stage/config.json" --perms 0600
    pct push "$CADDY_CT_ID" "$REPO_DIR/.secrets/panel-admin.json" "$stage/auth.json" --perms 0600
    pct push "$CADDY_CT_ID" "$TEMP_DIR/panel.tar" "$stage/panel.tar" --perms 0600
    pct push "$CADDY_CT_ID" "$REPO_DIR/scripts/ndi_sdk.py" "$stage/ndi_sdk.py" --perms 0600
    pct push "$CADDY_CT_ID" "$TEMP_DIR/revision.json" "$stage/revision.json" --perms 0600
    pct exec "$CADDY_CT_ID" -- bash -s < "$REPO_DIR/cts/caddy/provision/panel.sh"
    pct exec "$CADDY_CT_ID" -- bash -s -- "$stage" < "$REPO_DIR/cts/caddy/provision/apply.sh"
    sync_panel_ndi
    configure_panel_updates || warn 'No se pudo activar la comprobación periódica de actualizaciones.'
    ok "Panel y Caddy aplicados. Acceso inicial en $REPO_DIR/.secrets/panel-admin.json."
}
