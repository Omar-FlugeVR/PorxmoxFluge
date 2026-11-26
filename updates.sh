#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "$SCRIPT_DIR/scripts/common.sh"
init_runtime

wait_lock=0
checkout="$REPO_DIR"
mode=report
case "${1:-}" in
    '') ;;
    --scheduled|--automatic) wait_lock=90; IFS= read -r checkout < "$REPO_DIR/checkout" ;;
    --jobs) wait_lock=90; mode='jobs'; IFS= read -r checkout < "$REPO_DIR/checkout" ;;
    --help|-h) printf '%s\n' 'Uso: bash updates.sh' 'Consulta versiones desde Proxmox y publica el resultado en el panel.'; exit 0 ;;
    *) die "Opción desconocida: $1" ;;
esac
prepare_host() {
    load_config "$checkout/config.env"
    [[ -n "${CADDY_IP:-}" ]] || die 'Configura primero el CT del panel.'
    require_proxmox "$wait_lock"
    verify_host_ip
}

if [[ "$mode" == jobs ]]; then prepare_host >/dev/null; else prepare_host; fi

check_ct() {
    local state
    state="$(ct_state "$CT_ID")" || return
    if [[ "$state" == new ]]; then
        printf 'not_installed'
    else
        [[ "$(pct status "$CT_ID")" == 'status: running' ]] || return 1
        verify_ct_config "$CT_ID"
        printf 'available'
    fi
}

if [[ "$mode" == jobs ]]; then
    ct_settings caddy
    [[ "$(check_ct)" == available ]] || exit 0
    python3 "$REPO_DIR/scripts/run_updates.py" "$checkout" "$CADDY_CT_ID"
    exit 0
fi

# El archivo temporal y su sustitución se crean dentro del CT.
publish_update_state() {
    # shellcheck disable=SC2016
    timeout 15 pct exec "$CADDY_CT_ID" -- sh -eu -c '
        temp="$(mktemp "/etc/fluge-control/.$1.XXXXXXXX")"
        trap '\''rm -f -- "$temp"'\'' EXIT
        cat > "$temp"
        chmod 0644 "$temp"
        mv -fT -- "$temp" "/etc/fluge-control/$1"
    ' sh "$1"
}

if [[ "${1:-}" == --automatic ]]; then
    ct_settings caddy
    [[ "$(check_ct)" == available ]] || die 'El CT del panel no está accesible o no coincide con config.env.'
    # La caducidad cubre una terminación forzada. Coincide con TimeoutStartSec.
    if publish_update_state update-check.json <<< "{\"expiresAt\":$(( $(date +%s) + 180 ))}"; then
        trap 'publish_update_state update-check.json <<< "{}" || warn "No se pudo cerrar el estado de la búsqueda automática."; cleanup' EXIT
    else
        warn 'No se pudo anunciar la búsqueda automática al panel.'
    fi
fi

versions=()
for role in "${ALL_CTS[@]}"; do
    ip_key="${role^^}_IP"
    state=not_installed
    version=''
    if [[ -n "${!ip_key:-}" ]]; then
        ct_settings "$role"
        state="$(check_ct)" || state=unavailable
    fi
    if [[ "$state" == available ]]; then
        case "$role" in
            caddy)
                panel_revision="$(timeout 8 pct exec "$CT_ID" -- cat /opt/fluge-control/revision.json)" || panel_revision='{}'
                version="$(timeout 8 pct exec "$CT_ID" -- caddy version)" || version=''
                ;;
            adguard) version="$(timeout 8 pct exec "$CT_ID" -- /opt/AdGuardHome/AdGuardHome --version)" || version='' ;;
            companion) version="$(timeout 8 pct exec "$CT_ID" -- cat /opt/companion/BUILD)" || version='' ;;
            ndi)
                # La versión de la biblioteca corresponde al SDK que aportó el binario.
                version="$(timeout 8 pct exec "$CT_ID" -- sh -ec 'test -x /opt/ndi-discovery/bin/ndi-discovery-server; readlink -e /opt/ndi-discovery/lib/libndi.so.6 || printf installed')" || version=''
                ;;
        esac
    elif [[ "$role" == caddy ]]; then
        die 'El CT del panel no está accesible o no coincide con config.env.'
    fi
    versions+=("$role" "$state" "$version")
done

report="$TEMP_DIR/updates.json"
python3 "$REPO_DIR/scripts/check_updates.py" "$checkout" "$panel_revision" "${versions[@]}" > "$report"
publish_update_state updates.json < "$report"
ok 'Informe de versiones publicado en Actualizaciones.'
