#!/usr/bin/env bash
# Respuestas para la revisión fijada del post-install comunitario de Proxmox VE.

post_install_choice() {
    local title="$1" message="$2" answer tag
    local -a tags=()
    shift 2
    while [[ "$#" -ge 2 && "$1" != --default-item ]]; do
        tags+=("$1")
        shift 2
    done
    case "$title" in
        PVE-ENTERPRISE|CEPH-ENTERPRISE)
            if [[ " ${tags[*]} " == *' disable '* ]]; then answer=disable
            elif [[ "$message" == *"Disable "* ]]; then answer=yes
            else answer=no; fi ;;
        PVE-NO-SUBSCRIPTION)
            if [[ " ${tags[*]} " == *' enable '* ]]; then answer=enable
            elif [[ " ${tags[*]} " == *' keep '* ]]; then answer=keep
            else answer=yes; fi ;;
        'CEPH PACKAGE REPOSITORIES')
            # PVE 8 ofrece corregir ceph.list. PVE 9 ofrece añadir nuevas fuentes.
            if [[ "$message" == *"Correct "* ]]; then answer=yes; else answer=no; fi ;;
        'SUBSCRIPTION NAG'|UPDATE) answer=yes ;;
        SOURCES|PVETEST|'HIGH AVAILABILITY'|COROSYNC|REBOOT) answer=no ;;
        *) return 64 ;;
    esac
    for tag in "${tags[@]}"; do
        if [[ "$tag" == "$answer" ]]; then
            printf '%s\n' "$answer"
            return 0
        fi
    done
    return 64
}

run_community_post_install() (
    trap - EXIT
    local revision=11acd8db754db96c9088762e2deff9c16f0a35f7
    local sha256=e6b7a97b0fd6646f7f1e2c3e199d72dd84770ce43bd2a20613260f2a146501b9
    local script="$TEMP_DIR/post-pve-install.sh"
    need_commands curl sha256sum apt-get
    curl --fail --silent --show-error --location --retry 3 \
        "https://raw.githubusercontent.com/community-scripts/ProxmoxVE/$revision/tools/pve/post-pve-install.sh" \
        --output "$script"
    printf '%s  %s\n' "$sha256" "$script" | sha256sum --check --status \
        || die 'El post-install descargado no coincide con la revisión comprobada.'

    # La revisión oficial carga un helper de telemetría desde una rama móvil.
    # Usar un helper vacío local para que el perfil solo ejecute el script fijado.
    mkdir -p "$TEMP_DIR/post-install-core/api"
    : > "$TEMP_DIR/post-install-core/api/api.func"
    export COMMUNITY_SCRIPTS_CORE_URL="file://$TEMP_DIR/post-install-core"
    export DEBIAN_FRONTEND=noninteractive
    exec 8>&2
    local indexes_ready=0

    # Estas funciones se invocan desde el script comunitario que se carga abajo.
    # shellcheck disable=SC2317,SC2329
    whiptail() {
        local title='' message='' mode='' answer
        while [[ "$#" -gt 0 ]]; do
            case "$1" in
                --backtitle) shift 2 ;;
                --title) title="$2"; shift 2 ;;
                --msgbox) mode=message; shift ;;
                --yesno) mode=yesno; break ;;
                --menu) mode=menu; message="$2"; shift 5; break ;;
                *) break ;;
            esac
        done
        case "$mode:$title" in
            'yesno:Disable legacy sources?') return 1 ;;
            'message:Deb822 sources detected'|'message:Support Subscriptions'|'message:Post-Install Reminder')
                return 0 ;;
            menu:*)
                answer="$(post_install_choice "$title" "$message" "$@")" || {
                    printf '[ERROR] Menú inesperado del post-install: %s\n' "$title" >&8
                    return 64
                }
                printf '%s\n' "$answer" >&2 ;;
            *) printf '[ERROR] Diálogo inesperado del post-install: %s\n' "$title" >&8; return 64 ;;
        esac
    }

    # shellcheck disable=SC2317,SC2329
    reboot() {
        printf '[ERROR] El perfil no permite reiniciar el host.\n' >&8
        exit 1
    }

    # shellcheck disable=SC2317,SC2329
    apt() {
        # Actualizar el índice antes de reinstalar el widget del aviso.
        if [[ "$indexes_ready" == 0 ]]; then
            apt-get -o APT::Update::Error-Mode=any update >&8 2>&1 || exit "$?"
            indexes_ready=1
        fi
        if [[ "$1" == update ]]; then return 0; fi
        apt-get -y "$@" >&8 2>&1 || exit "$?"
    }

    # shellcheck source=/dev/null
    source "$script" <<< 'y'
)
