#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "$SCRIPT_DIR/scripts/common.sh"
init_runtime
MODE=apply
TARGET_CT=all
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --ct) [[ "$#" -ge 2 ]] || die 'Falta el valor de --ct.'; TARGET_CT="$2"; shift ;;
        --yes) ASSUME_YES=1 ;;
        --check) [[ "$MODE" == apply ]] || die 'Elige --check o --render-only.'; MODE=check ;;
        --render-only) [[ "$MODE" == apply ]] || die 'Elige --check o --render-only.'; MODE=render ;;
        --help|-h)
            printf '%s\n' 'Uso: ./sync.sh [--ct all|adguard|caddy|companion|ndi] [--yes | --check | --render-only]' \
                '  --ct CT        Sincronizar solo el CT indicado (por defecto: all).' \
                '  --check        Validar config.env y la semilla del CT seleccionado, sin modificar el host.' \
                '  --render-only  Generar la semilla en ./generated sin acceder a Proxmox.' \
                '  --yes          Aplicar configuración sin confirmación; nunca crear CTs.'
            exit 0 ;;
        *) die "Opción desconocida: $1" ;;
    esac
    shift
done
load_config
select_configured_cts "$TARGET_CT"
if [[ "$MODE" == check ]]; then
    python3 "$REPO_DIR/scripts/render.py" --services "$REPO_DIR/services.json" --ct "$TARGET_CT"
    exit 0
fi
if [[ "$MODE" == render ]]; then
    render_selected "$REPO_DIR/generated" seed "${SELECTED_CTS[@]}"
    ok "Configuración de ${SELECTED_CTS[*]} generada en $REPO_DIR/generated/."
    exit 0
fi
require_proxmox
verify_host_ip
for role in "${SELECTED_CTS[@]}"; do
    ct_settings "$role"
    [[ "$(ct_state "$CT_ID")" == existing ]] || die "Falta el CT $CT_ID. Ejecuta ./install.sh."
    [[ "$(pct status "$CT_ID")" == 'status: running' ]] || die "CT $CT_ID detenido. Arráncalo con pct start $CT_ID y repite."
    verify_ct "$CT_ID"
    assert_services "$role"
done
render_selected "$TEMP_DIR/rendered" live "${SELECTED_CTS[@]}"
info "Se actualizará la configuración de: ${SELECTED_CTS[*]}."
if [[ " ${SELECTED_CTS[*]} " == *' adguard '* ]]; then
    info 'AdGuard se reiniciará brevemente si cambian las reescrituras DNS.'
fi
confirm '¿Aplicar la configuración generada?' yes || { info 'Sin cambios.'; exit 0; }
for role in "${APPLY_ORDER[@]}"; do
    if [[ " ${SELECTED_CTS[*]} " == *" $role "* ]]; then "apply_$role"; fi
done
ok "Sincronización de ${SELECTED_CTS[*]} completada."
