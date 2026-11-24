#!/usr/bin/env bash
set -euo pipefail

ndi_settings() {
    # Consumidas por el coordinador Proxmox.
    # shellcheck disable=SC2034
    CT_ID="$NDI_CT_ID" CT_IP="$NDI_IP" CT_HOSTNAME="$NDI_HOSTNAME" CT_CORES=1 CT_RAM=512 CT_DISK=4 CT_ORDER=4
}

ndi_preflight() {
    local accepted
    accepted="$(python3 "$REPO_DIR/scripts/ndi_installer.py" "$TEMP_DIR/ndi-installer")" || return
    if [[ "$accepted" != "${NDI_LICENSE_SHA256:-}" ]]; then
        export NDI_LICENSE_SHA256="$accepted"
        # Consumido por install.sh para persistir una aceptación nueva.
        # shellcheck disable=SC2034
        NDI_LICENSE_CHANGED=1
    fi
}

ndi_has_services() {
    pct exec "$NDI_CT_ID" -- bash -c 'test -x /opt/ndi-discovery/bin/ndi-discovery-server && command -v python3 >/dev/null && id ndi >/dev/null && systemctl cat ndi-discovery.service >/dev/null'
}

ndi_assert_services() {
    ndi_has_services || die "CT $NDI_CT_ID necesita NDI Discovery Service en /opt/ndi-discovery y la unidad ndi-discovery.service."
}

provision_ndi() {
    local stage checksum installer="$TEMP_DIR/ndi-installer/vendor-install.sh"
    checksum="$(sha256sum -- "$installer")"
    stage="$(pct exec "$NDI_CT_ID" -- mktemp -d /root/studio-ndi-install.XXXXXXXX)"
    pct push "$NDI_CT_ID" "$REPO_DIR/cts/ndi/provision/install.sh" "$stage/install.sh" --perms 0700
    pct push "$NDI_CT_ID" "$installer" "$stage/vendor-install.sh" --perms 0600
    pct exec "$NDI_CT_ID" -- bash "$stage/install.sh" "$stage" "$TIMEZONE" "${checksum%% *}" </dev/null
    ok "NDI Discovery instalado de forma nativa en CT $NDI_CT_ID."
}

apply_ndi() {
    apply_native_service ndi
    sync_panel_ndi
}
