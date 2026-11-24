#!/usr/bin/env bash
set -euo pipefail

ndi_settings() {
    # Consumidas por el coordinador Proxmox.
    # shellcheck disable=SC2034
    CT_ID="$NDI_CT_ID" CT_IP="$NDI_IP" CT_HOSTNAME="$NDI_HOSTNAME" CT_CORES=1 CT_RAM=512 CT_DISK=4 CT_ORDER=4
}

ndi_preflight() {
    [[ -t 0 && -t 1 ]] || die 'Un NDI nuevo requiere una terminal para mostrar y aceptar la licencia oficial. Ejecuta el instalador desde la consola Proxmox o SSH interactivo; --yes no acepta licencias.'
    if [[ -n "${NDI_INSTALLER_FILE:-}" ]]; then
        if [[ "$NDI_INSTALLER_FILE" != /* ]]; then NDI_INSTALLER_FILE="$REPO_DIR/$NDI_INSTALLER_FILE"; fi
        [[ -r "$NDI_INSTALLER_FILE" && -f "$NDI_INSTALLER_FILE" ]] || die "No se puede leer NDI_INSTALLER_FILE=$NDI_INSTALLER_FILE en el host."
    fi
}

ndi_assert_services() {
    pct exec "$NDI_CT_ID" -- bash -c 'test -x /opt/ndi-discovery/bin/ndi-discovery-server && command -v python3 >/dev/null && id ndi >/dev/null && systemctl cat ndi-discovery.service >/dev/null' \
        || die "CT $NDI_CT_ID necesita NDI Discovery Service en /opt/ndi-discovery y la unidad ndi-discovery.service."
}

provision_ndi() {
    local stage source=download
    stage="$(pct exec "$NDI_CT_ID" -- mktemp -d /root/studio-ndi-install.XXXXXXXX)"
    pct push "$NDI_CT_ID" "$REPO_DIR/cts/ndi/provision/install.sh" "$stage/install.sh" --perms 0700
    if [[ -n "${NDI_INSTALLER_FILE:-}" ]]; then
        pct push "$NDI_CT_ID" "$NDI_INSTALLER_FILE" "$stage/installer.download" --perms 0600
        source=local
    fi
    info 'El instalador oficial NDI mostrará su licencia; lee el texto y responde en esta terminal.'
    # No redirigir stdin aquí: la licencia debe leer la respuesta del usuario.
    pct exec "$NDI_CT_ID" -- bash "$stage/install.sh" "$stage" "$TIMEZONE" "$source" \
        "${NDI_INSTALLER_URL:-https://downloads.ndi.tv/SDK/NDI_SDK_Linux/Install_NDI_SDK_v6_Linux.tar.gz}" "${NDI_INSTALLER_SHA256:-}"
    ok "NDI Discovery instalado de forma nativa en CT $NDI_CT_ID."
}

apply_ndi() {
    apply_native_service ndi
}
