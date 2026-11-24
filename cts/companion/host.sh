#!/usr/bin/env bash
set -euo pipefail

companion_settings() {
    # Consumidas por el coordinador Proxmox.
    # shellcheck disable=SC2034
    CT_ID="$COMPANION_CT_ID" CT_IP="$COMPANION_IP" CT_HOSTNAME="$COMPANION_HOSTNAME" CT_CORES=2 CT_RAM=2048 CT_DISK=8 CT_ORDER=3
}

companion_assert_services() {
    pct exec "$COMPANION_CT_ID" -- bash -c 'test -x /usr/local/src/companionpi/launch.sh && test -f /opt/companion/main.js && command -v python3 >/dev/null && id companion >/dev/null && systemctl cat companion.service >/dev/null' \
        || die "CT $COMPANION_CT_ID necesita Companion instalado mediante el instalador oficial companion-pi."
}

provision_companion() {
    pct exec "$COMPANION_CT_ID" -- bash -s -- "$TIMEZONE" "$COMPANION_BUILD" \
        < "$REPO_DIR/cts/companion/provision/install.sh"
    ok "Companion instalado de forma nativa en CT $COMPANION_CT_ID."
}

apply_companion() {
    apply_native_service companion
}
