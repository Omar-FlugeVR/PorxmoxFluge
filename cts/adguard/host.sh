#!/usr/bin/env bash
set -euo pipefail

adguard_settings() {
    # Consumidas por create_ct/verify_ct en scripts/common.sh.
    # shellcheck disable=SC2034
    CT_ID="$ADGUARD_CT_ID" CT_IP="$ADGUARD_IP" CT_HOSTNAME="$ADGUARD_HOSTNAME" CT_CORES=1 CT_RAM=512 CT_DISK=4 CT_ORDER=1
}

adguard_assert_services() {
    pct exec "$ADGUARD_CT_ID" -- bash -c 'test -x /opt/AdGuardHome/AdGuardHome && test -s /opt/AdGuardHome/AdGuardHome.yaml && systemctl cat AdGuardHome.service >/dev/null && python3 -c "import yaml"' \
        || die "CT $ADGUARD_CT_ID necesita AdGuard Home oficial en /opt/AdGuardHome y python3-yaml."
}

provision_adguard() {
    local secrets_dir="$REPO_DIR/.secrets"
    mkdir -p -- "$secrets_dir"
    chmod 700 -- "$secrets_dir"
    python3 - "$secrets_dir/adguard-admin.json" "$ADGUARD_ADMIN_USER" <<'PY'
import json, os, secrets, sys
path, username = sys.argv[1:]
if not os.path.exists(path):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
        json.dump({'username': username, 'password': secrets.token_urlsafe(24)}, out)
        out.write('\n')
os.chmod(path, 0o600)
PY
    pct push "$ADGUARD_CT_ID" "$secrets_dir/adguard-admin.json" /root/.studio-adguard-admin.json --perms 0600
    pct exec "$ADGUARD_CT_ID" -- bash -s -- "$TIMEZONE" "$UPSTREAM_DNS" \
        < "$REPO_DIR/cts/adguard/provision/install.sh"
    ok "AdGuard instalado. Credenciales en $secrets_dir/adguard-admin.json (solo root)."
}

apply_adguard() {
    local stage
    stage="$(pct exec "$ADGUARD_CT_ID" -- mktemp -d /tmp/studio-adguard.XXXXXXXX)"
    pct push "$ADGUARD_CT_ID" "$TEMP_DIR/rendered/adguard/rewrites.json" "$stage/rewrites.json" --perms 0600
    # El script elimina el staging en su bloque finally, también si hay un error.
    pct exec "$ADGUARD_CT_ID" -- python3 - "$stage/rewrites.json" \
        < "$REPO_DIR/cts/adguard/provision/apply.py"
    if [[ -n "${CADDY_IP:-}" && "$(pct status "$CADDY_CT_ID" 2>/dev/null || true)" == 'status: running' ]] \
        && pct exec "$CADDY_CT_ID" -- test -f /opt/fluge-control/bin/adopt-dns.php 2>/dev/null; then
        pct exec "$CADDY_CT_ID" -- runuser -u www-data -- php /opt/fluge-control/bin/adopt-dns.php \
            < "$TEMP_DIR/rendered/adguard/rewrites.json"
    fi
    ok 'Reescrituras DNS de AdGuard actualizadas.'
}
