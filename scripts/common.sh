#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ASSUME_YES=0
TEMP_DIR=''
# Orden de creación/arranque. DNS se publica después de las aplicaciones.
ALL_CTS=(adguard caddy companion ndi)
# Consumido por install.sh y sync.sh.
# shellcheck disable=SC2034
APPLY_ORDER=(companion ndi caddy adguard)

if [[ -t 2 && -z "${NO_COLOR:-}" ]]; then
    GREEN=$'\033[32m' BLUE=$'\033[36m' YELLOW=$'\033[33m' RED=$'\033[31m' RESET=$'\033[0m'
else
    GREEN='' BLUE='' YELLOW='' RED='' RESET=''
fi
info() { printf '%s[INFO]%s %s\n' "$BLUE" "$RESET" "$*" >&2; }
ok() { printf '%s[OK]%s %s\n' "$GREEN" "$RESET" "$*" >&2; }
warn() { printf '%s[AVISO]%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die() { printf '%s[ERROR]%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

cleanup() {
    if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
        rm -rf -- "$TEMP_DIR"
    fi
}

init_runtime() {
    set -E
    umask 077
    trap cleanup EXIT
    trap 'printf "[ERROR] Fallo en %s:%s (código %s). No se elimina ningún CT.\n" "${BASH_SOURCE[0]}" "$LINENO" "$?" >&2' ERR
    trap 'exit 130' INT
    trap 'exit 143' TERM
}

need_commands() {
    local command
    for command in "$@"; do
        command -v "$command" >/dev/null 2>&1 || die "Falta el comando $command. Consulta los requisitos en README.md."
    done
}

source_config() {
    # Archivo local de confianza, no un inventario recibido por la red.
    set -a
    # shellcheck source=/dev/null
    source "$1"
    set +a
}

load_config() {
    local file="$1"
    [[ -f "$file" ]] || die 'Ejecuta ./install.sh en una terminal para generar config.env o prepara el archivo a partir del ejemplo.'
    source_config "$file"
    need_commands python3
    python3 "$REPO_DIR/scripts/render.py" --config-only
    BASE_DOMAIN="${BASE_DOMAIN#.}"
    BASE_DOMAIN="${BASE_DOMAIN%.}"
    BASE_DOMAIN="${BASE_DOMAIN,,}"
    export BASE_DOMAIN
}

confirm() {
    local prompt="$1" default="${2:-no}" answer
    if [[ "$ASSUME_YES" == 1 ]]; then
        return 0
    fi
    [[ -t 0 ]] || die 'Se requiere una terminal interactiva; usa --yes para las acciones documentadas.'
    if [[ "$default" == yes ]]; then
        read -r -p "$prompt [S/n] " answer || return 1
        answer="${answer:-s}"
    else
        read -r -p "$prompt [s/N] " answer || return 1
    fi
    [[ "${answer,,}" =~ ^(s|si|sí|y|yes)$ ]]
}

require_proxmox() {
    [[ "${EUID}" == 0 ]] || die 'Ejecuta este script como root en el host Proxmox VE.'
    need_commands pct pveam pvesm pvesh pveversion ip flock
    [[ -d /etc/pve/local ]] || die 'No se encuentra /etc/pve/local: no parece un host Proxmox VE operativo.'
    pveversion
    exec 9>/run/lock/studio-infra.lock
    flock -n 9 || die 'Ya hay otra instalación o sincronización en curso.'
    TEMP_DIR="$(mktemp -d -t studio-infra.XXXXXXXX)"
}

verify_host_ip() {
    local host_addresses
    host_addresses="$(ip -o -4 addr show)"
    if ! awk -v ip="$PROXMOX_IP" 'split($4,a,"/") && a[1] == ip {found=1} END {exit !found}' <<< "$host_addresses"; then
        die "PROXMOX_IP=$PROXMOX_IP no está asignada a este host."
    fi
}

render_files() {
    local inventory="$REPO_DIR/services.json" target="${2:-all}" mode="${3:-live}" panel_state caddy_state
    local -a runtime_args=()
    [[ "$mode" == live || "$mode" == seed ]] || die "Modo de inventario desconocido: $mode"
    # La previsualización siempre es local. En producción nunca sustituir un
    # inventario inaccesible por la semilla: podría borrar reglas DNS vigentes.
    if [[ "$mode" == live && -n "${CADDY_IP:-}" && "$target" != companion && "$target" != ndi ]]; then
        caddy_state="$(ct_state "$CADDY_CT_ID")" || die 'No se puede verificar el CT del panel; no se publica la semilla.'
        if [[ "$caddy_state" == new ]]; then
            python3 "$REPO_DIR/scripts/render.py" --services "$inventory" --output-dir "$1" --ct "$target"
            return
        fi
        [[ "$caddy_state" == existing ]] || die 'Estado desconocido del CT del panel.'
        [[ "$(pct status "$CADDY_CT_ID")" == 'status: running' ]] \
            || die "Arranca el CT del panel con pct start $CADDY_CT_ID para conservar su inventario antes de publicar DNS o Caddy."
        verify_ct "$CADDY_CT_ID"
        panel_state="$(pct exec "$CADDY_CT_ID" -- bash -c '
            if test -e /var/lib/fluge-control/inventory.json || test -e /var/lib/fluge-control/config.json || test -e /var/lib/fluge-control/auth.json || test -d /opt/fluge-control; then
                if test -s /var/lib/fluge-control/inventory.json && test -s /var/lib/fluge-control/config.json && test -f /opt/fluge-control/bin/export.php; then
                    printf ready
                else printf incomplete; fi
            else printf new; fi')" || die 'No se puede comprobar el inventario del panel; no se publica la semilla.'
        case "$panel_state" in
            ready)
                [[ -n "$TEMP_DIR" ]] || TEMP_DIR="$(mktemp -d -t studio-infra.XXXXXXXX)"
                inventory="$TEMP_DIR/live-services.json"
                pct exec "$CADDY_CT_ID" -- runuser -u www-data -- php /opt/fluge-control/bin/export.php > "$inventory" \
                    || die 'No se puede exportar el inventario del panel; no se publica la semilla.'
                runtime_args=(--runtime)
                info 'Se conserva el inventario guardado desde el panel web.' ;;
            new) info 'Panel sin inicializar; se utiliza el inventario inicial services.json.' ;;
            *) die 'El inventario o el exportador del panel están incompletos. Recupera el panel antes de sincronizar; no se publica la semilla.' ;;
        esac
    fi
    python3 "$REPO_DIR/scripts/render.py" --services "$inventory" --output-dir "$1" --ct "$target" "${runtime_args[@]}"
}

select_cts() {
    # Los puntos de entrada consumen este array al instalar o sincronizar.
    # shellcheck disable=SC2034
    case "$1" in
        all) SELECTED_CTS=("${ALL_CTS[@]}") ;;
        adguard|caddy|companion|ndi) SELECTED_CTS=("$1") ;;
        *) die "CT desconocido: $1. Usa adguard, caddy, companion, ndi o all." ;;
    esac
}

select_configured_cts() {
    local target="$1" role ip_key
    select_cts "$target"
    if [[ "$target" == all && -n "${INSTALL_CTS:-}" ]]; then
        read -r -a SELECTED_CTS <<< "$INSTALL_CTS"
    fi
    local -a configured=()
    for role in "${SELECTED_CTS[@]}"; do
        ip_key="${role^^}_IP"
        if [[ -n "${!ip_key:-}" ]]; then configured+=("$role")
        elif [[ "$target" != all ]]; then die "El CT $role no está configurado. Usa ./install.sh --ct $role."; fi
    done
    SELECTED_CTS=("${configured[@]}")
}

render_selected() {
    local output="$1" mode="$2" role
    shift 2
    if [[ " $* " == *' caddy '* && " $* " == *' adguard '* ]]; then
        render_files "$output" all "$mode"
    else
        for role in "$@"; do render_files "$output" "$role" "$mode"; done
    fi
}

ct_state() {
    # Consultar el clúster también evita colisiones con VMs o CTs de otro nodo.
    local resources node
    resources="$(pvesh get /cluster/resources --type vm --output-format json)" || die 'No se puede consultar el inventario del clúster Proxmox.'
    node="$(basename -- "$(readlink -f /etc/pve/local)")"
    python3 - "$1" "$node" "$resources" <<'PY'
import json, sys
vmid, node, raw = sys.argv[1:]
items = [x for x in json.loads(raw) if str(x.get('vmid')) == vmid]
if not items:
    print('new')
elif len(items) == 1 and items[0].get('type') == 'lxc' and items[0].get('node') == node:
    print('existing')
else:
    sys.exit(f'ID {vmid} ocupado por una VM o por un CT de otro nodo. Elige otro ID.')
PY
}

ct_settings() {
    case "$1" in
        adguard|caddy|companion|ndi) "${1}_settings" ;;
        *) die "Rol de CT desconocido: $1" ;;
    esac
}

verify_ct_config() {
    local id="$1" config
    config="$(pct config "$id")" || die "No se puede leer la configuración del CT $id."
    python3 "$REPO_DIR/scripts/ct-network.py" "$id" --verify <<< "$config" \
        || die "La identidad del CT $id no coincide; no se modifica."
}

verify_ct() {
    local id="$1"
    verify_ct_config "$id"
    # Las variables de os-release se expanden dentro del CT.
    # shellcheck disable=SC2016
    pct exec "$id" -- bash -c 'source /etc/os-release; [[ "$ID" == debian && "$VERSION_ID" == 12 ]]' \
        || die "El CT $id debe ejecutar Debian 12."
}

start_ct() {
    local id="$1" state attempt
    state="$(pct status "$id")"
    if [[ "$state" != 'status: running' ]]; then
        info "Arrancando CT $id..."
        pct start "$id"
    fi
    for ((attempt=0; attempt<60; attempt++)); do
        state="$(pct exec "$id" -- systemctl is-system-running 2>/dev/null || true)"
        # Un sistema degraded puede tener los servicios necesarios operativos.
        if [[ "$state" == running || "$state" == degraded ]]; then
            return 0
        fi
        sleep 1
    done
    die "El CT $id no está listo; revisa pct console $id."
}

check_storage() {
    local storage="$1" content="$2" status
    status="$(pvesm status --storage "$storage" --content "$content")"
    awk -v storage="$storage" '$1==storage && $3=="active" {found=1} END {exit !found}' <<< "$status" \
        || die "El storage $storage no está activo o no permite $content."
}

ensure_template() {
    check_storage "$TEMPLATE_STORAGE" vztmpl
    check_storage "$DISK_STORAGE" rootdir
    local templates available filename
    templates="$(pveam list "$TEMPLATE_STORAGE")"
    TEMPLATE_VOLID="$(awk '$1 ~ /:vztmpl\/debian-12-standard_[^ ]*_amd64\.tar\.(zst|gz|xz)$/ {print $1}' <<< "$templates" | sort -V | tail -n 1)"
    if [[ -n "$TEMPLATE_VOLID" ]]; then
        ok "Plantilla disponible: $TEMPLATE_VOLID"
        return
    fi
    info 'Actualizando catálogo y descargando Debian 12...'
    pveam update
    available="$(pveam available --section system)"
    filename="$(awk '$2 ~ /^debian-12-standard_.*_amd64\.tar\.(zst|gz|xz)$/ {print $2}' <<< "$available" | sort -V | tail -n 1)"
    [[ -n "$filename" ]] || die 'No hay plantilla oficial Debian 12 amd64 en el catálogo; no se sustituye por otra distribución.'
    pveam download "$TEMPLATE_STORAGE" "$filename"
    TEMPLATE_VOLID="$TEMPLATE_STORAGE:vztmpl/$filename"
}

create_ct() {
    local role="$1" nics nic index=0
    local -a options=()
    ct_settings "$role"
    [[ "$(ct_state "$CT_ID")" == new ]] || die "El ID $CT_ID ha pasado a estar ocupado."
    nics="$(python3 "$REPO_DIR/scripts/ct-network.py" "$CT_ID")"
    while IFS= read -r nic; do
        options+=("--net$index" "$nic")
        index=$((index + 1))
    done <<< "$nics"
    info "Creando $role: CT $CT_ID, $CT_IP, $index NIC, $CT_CORES CPU, ${CT_RAM} MiB, $CT_DISK GiB."
    pct create "$CT_ID" "$TEMPLATE_VOLID" \
        --ostype debian --arch amd64 --hostname "$CT_HOSTNAME" \
        --unprivileged 1 --cores "$CT_CORES" --memory "$CT_RAM" --swap 0 \
        --rootfs "$DISK_STORAGE:$CT_DISK" --onboot 1 --startup "order=$CT_ORDER,up=15" \
        "${options[@]}" \
        --nameserver "$BOOTSTRAP_DNS" --searchdomain "$BASE_DOMAIN" \
        --description "studio-infra:$role - Debian 12 nativo, gestionado desde este repositorio"
    start_ct "$CT_ID"
    verify_ct "$CT_ID"
}

assert_services() {
    case "$1" in
        adguard|caddy|companion|ndi) "${1}_assert_services" ;;
        *) die "Rol de CT desconocido: $1" ;;
    esac
}

preflight_new_ct() {
    local hook="${1}_preflight"
    ct_settings "$1"
    python3 "$REPO_DIR/scripts/ct-network.py" "$CT_ID" --check-bridges
    if declare -F "$hook" >/dev/null; then "$hook"; fi
}

apply_native_service() {
    local role="$1" stage file
    ct_settings "$role"
    stage="$(pct exec "$CT_ID" -- mktemp -d "/tmp/studio-$role.XXXXXXXX")"
    for file in "$TEMP_DIR/rendered/$role/"*; do
        pct push "$CT_ID" "$file" "$stage/$(basename -- "$file")" --perms 0600
    done
    pct exec "$CT_ID" -- python3 - "$stage/apply.json" < "$REPO_DIR/scripts/provision/apply-service.py"
    ok "Configuración de $role aplicada y servicio comprobado."
}

# Los detalles de cada CT pertenecen a su módulo; aquí solo se comparten utilidades.
# shellcheck source=scripts/post-install.sh
source "$REPO_DIR/scripts/post-install.sh"
# shellcheck source=cts/adguard/host.sh
source "$REPO_DIR/cts/adguard/host.sh"
# shellcheck source=cts/caddy/host.sh
source "$REPO_DIR/cts/caddy/host.sh"
# shellcheck source=cts/companion/host.sh
source "$REPO_DIR/cts/companion/host.sh"
# shellcheck source=cts/ndi/host.sh
source "$REPO_DIR/cts/ndi/host.sh"
