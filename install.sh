#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "$SCRIPT_DIR/scripts/common.sh"
init_runtime

show_installer_header() {
    if [[ -t 1 ]]; then
        if [[ "${TERM:-dumb}" != dumb ]]; then printf '\033[2J\033[H'; fi
        cat <<'BANNER'
 ______ _      _    _  _____ ______
|  ____| |    | |  | |/ ____|  ____|
| |__  | |    | |  | | |  __| |__
|  __| | |    | |  | | | |_ |  __|
| |    | |____| |__| | |__| | |____
|_|    |______|\____/ \_____|______|

Instalador de servicios para Proxmox VE

BANNER
    fi
}

usage() {
    cat <<'HELP'
Uso: ./install.sh [--ct all|adguard|caddy|companion|ndi] [--from-config | --yes] [--update-existing] [--post-install]
  Sin opciones       Cuestionario interactivo y generación de config.env.
  --ct CT            Limitar el cuestionario o la instalación a un CT.
  --from-config      Usar config.env con confirmaciones y sin cuestionario.
  --yes              Usar config.env y crear los CTs seleccionados ausentes sin preguntas.
  --update-existing  Autorizar solo configuración en CTs existentes compatibles.
  --post-install     Ofrecer el perfil comunitario de actualización sin reinicio.
                     Siempre requiere confirmación en una terminal, incluso con --yes.
  --help             Mostrar ayuda.
NDI nuevo: la licencia oficial requiere una terminal y aceptación manual, incluso con --yes.
Validar/previsualizar sin Proxmox: ./sync.sh --check / --render-only
HELP
}

POST_INSTALL=0
UPDATE_EXISTING=0
QUESTIONNAIRE=1
TARGET_CT=all
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --ct) [[ "$#" -ge 2 ]] || die 'Falta el valor de --ct.'; TARGET_CT="$2"; shift ;;
        --from-config) QUESTIONNAIRE=0 ;;
        --yes) ASSUME_YES=1; QUESTIONNAIRE=0 ;;
        --update-existing) UPDATE_EXISTING=1 ;;
        --post-install) POST_INSTALL=1 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; die "Opción desconocida: $1" ;;
    esac
    shift
done
select_cts "$TARGET_CT"

show_installer_header
require_proxmox
if [[ "$ASSUME_YES" == 0 || "$POST_INSTALL" == 1 ]]; then
    info 'Perfil del post-install: desactivar los repositorios Enterprise de PVE y Ceph, habilitar pve-no-subscription, quitar el aviso de suscripción y actualizar.'
    info 'Se conserva la configuración de HA y Corosync. Se omite pvetest y se pospone el reinicio del host.'
    info 'Se omite añadir repositorios de paquetes Ceph y se desactivan las entradas Ceph Enterprise existentes.'
    saved_yes="$ASSUME_YES"
    ASSUME_YES=0
    if confirm '¿Ejecutar el post-install comunitario de Proxmox VE?' no; then
        run_community_post_install
        show_installer_header
    fi
    ASSUME_YES="$saved_yes"
fi

candidate="$TEMP_DIR/config.env"
if [[ "$QUESTIONNAIRE" == 1 ]]; then
    need_commands python3
    questionnaire_options=()
    if [[ -f "$REPO_DIR/config.env" ]]; then
        source_config "$REPO_DIR/config.env"
        questionnaire_options=(--existing)
    fi
    python3 "$REPO_DIR/scripts/questionnaire.py" "$candidate" "${SELECTED_CTS[@]}" "${questionnaire_options[@]}"
    [[ -f "$candidate" ]] || exit 0
    load_config "$candidate"
else
    load_config "$REPO_DIR/config.env"
fi
select_configured_cts "$TARGET_CT"
verify_host_ip

declare -A ACTIONS
for role in "${ALL_CTS[@]}"; do ACTIONS[$role]=skip; done
needs_template=0
active_roles=()
for role in "${SELECTED_CTS[@]}"; do
    ct_settings "$role"
    state="$(ct_state "$CT_ID")"
    ACTIONS[$role]=skip
    if [[ "$state" == new ]]; then
        if [[ "$QUESTIONNAIRE" == 1 ]] || confirm "¿Crear el CT $CT_ID para $role?" yes; then
            ACTIONS[$role]=create
            needs_template=1
        fi
    else
        warn "El CT $CT_ID ya existe. No se recreará ni se cambiarán sus recursos o su red."
        if [[ "$QUESTIONNAIRE" == 1 ]]; then verify_ct_config "$CT_ID"; fi
        if [[ "$UPDATE_EXISTING" == 1 ]]; then
            ACTIONS[$role]=update
        elif [[ "$ASSUME_YES" == 0 ]] && confirm "¿Actualizar solo la configuración de $role en CT $CT_ID?" no; then
            ACTIONS[$role]=update
        fi
    fi
    if [[ "${ACTIONS[$role]}" != skip ]]; then
        active_roles+=("$role")
        operation=Instalar
        if [[ "${ACTIONS[$role]}" == update ]]; then operation=Actualizar; fi
        info "$operation · $role · CT $CT_ID ($CT_IP) · $CT_CORES CPU / $CT_RAM MiB / $CT_DISK GiB."
    fi
done

if [[ "${#active_roles[@]}" == 0 ]]; then info 'No hay CTs por instalar o actualizar.'; exit 0; fi

# Comprobar requisitos especiales (incluida la licencia NDI) antes de crear CTs.
for role in "${active_roles[@]}"; do
    if [[ "${ACTIONS[$role]}" == create ]]; then preflight_new_ct "$role"; fi
    if [[ "${ACTIONS[$role]}" == update && "$QUESTIONNAIRE" == 0 ]]; then
        ct_settings "$role"
        verify_ct_config "$CT_ID"
    fi
done

if [[ "$needs_template" == 1 ]]; then
    check_storage "$TEMPLATE_STORAGE" vztmpl
    check_storage "$DISK_STORAGE" rootdir
fi
if [[ "$QUESTIONNAIRE" == 1 ]]; then
    confirm '¿Guardar config.env y aplicar este plan de instalación?' no || { info 'Instalación de CTs cancelada.'; exit 0; }
fi

# Verificar los existentes seleccionados antes de crear cualquier CT nuevo.
for role in "${active_roles[@]}"; do
    if [[ "${ACTIONS[$role]}" == update ]]; then
        ct_settings "$role"
        start_ct "$CT_ID"
        verify_ct "$CT_ID"
        assert_services "$role"
    fi
done
render_selected "$TEMP_DIR/rendered" live "${active_roles[@]}"
if [[ "$QUESTIONNAIRE" == 1 ]]; then
    if [[ -f "$REPO_DIR/config.env" ]] && ! cmp -s "$candidate" "$REPO_DIR/config.env"; then
        install -m 0600 -- "$REPO_DIR/config.env" "$REPO_DIR/config.env.bak"
    fi
    staged_config="$(mktemp "$REPO_DIR/.config.env.XXXXXXXX")"
    if ! install -m 0600 -- "$candidate" "$staged_config" || ! mv -fT -- "$staged_config" "$REPO_DIR/config.env"; then
        rm -f -- "$staged_config"
        die 'No se pudo guardar config.env.'
    fi
    ok "Configuración guardada en $REPO_DIR/config.env."
fi
if [[ "$needs_template" == 1 ]]; then ensure_template; fi

for role in "${SELECTED_CTS[@]}"; do
    if [[ "${ACTIONS[$role]}" == create ]]; then
        create_ct "$role"
        "provision_$role"
    elif [[ "${ACTIONS[$role]}" == skip ]]; then
        info "$role omitido."
    fi
done

# Publicar las redirecciones antes de anunciar sus nombres mediante DNS.
for role in "${APPLY_ORDER[@]}"; do
    if [[ "${ACTIONS[$role]}" != skip ]]; then "apply_$role"; fi
done
ok "Instalación completada para: ${active_roles[*]}."
if [[ "${ACTIONS[adguard]}" != skip ]]; then info "AdGuard en http://$ADGUARD_IP/."; fi
if [[ "${ACTIONS[caddy]}" != skip ]]; then info "Panel en http://$CADDY_IP/ o http://$LANDING_SUBDOMAIN.$BASE_DOMAIN/ con DNS local."; fi
