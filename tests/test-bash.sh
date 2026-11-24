#!/usr/bin/env bash
# Pruebas con dobles de los comandos Proxmox; no requieren root ni un host PVE.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/common.sh
source "$ROOT/scripts/common.sh"
set -a
# shellcheck source=config.env.example
source "$ROOT/config.env.example"
set +a

# La selección de un CT no debe incluir al otro.
select_cts adguard
[[ "${SELECTED_CTS[*]}" == adguard ]]
select_cts caddy
[[ "${SELECTED_CTS[*]}" == caddy ]]
select_cts companion
[[ "${SELECTED_CTS[*]}" == companion ]]
select_cts ndi
[[ "${SELECTED_CTS[*]}" == ndi ]]
select_cts all
[[ "${SELECTED_CTS[*]}" == 'adguard caddy companion ndi' ]]
if (select_cts desconocido); then
    printf 'Se ha aceptado un CT desconocido.\n' >&2
    exit 1
fi

# Comprobar los cuatro pct create reales usando un doble, sin ejecutar Proxmox.
calls_file="$(mktemp)"
trap 'rm -f -- "$calls_file"' EXIT
ct_state() { printf 'new\n'; }
start_ct() { :; }
verify_ct() { :; }
pct() {
    [[ "$1" == create && "$(umask)" == 0022 ]] || return 99
    printf '%s\n' "$*" >> "$calls_file"
}
TEMPLATE_VOLID=local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst
(
    umask 077
    for role in "${ALL_CTS[@]}"; do
        create_ct "$role"
        [[ "$(umask)" == 0077 ]]
    done
)
[[ "$(wc -l < "$calls_file" | tr -d ' ')" == 4 ]]
grep -q 'create 110 .*--unprivileged 1 --cores 1 --memory 512.*--rootfs local-lvm:4' "$calls_file"
grep -q 'create 111 .*--unprivileged 1 --cores 1 --memory 512.*--rootfs local-lvm:4' "$calls_file"
grep -q 'create 112 .*--unprivileged 1 --cores 2 --memory 2048.*--rootfs local-lvm:8' "$calls_file"
grep -q 'create 113 .*--unprivileged 1 --cores 1 --memory 512.*--rootfs local-lvm:4' "$calls_file"
(
    export COMPANION_NICS='[{"bridge":"vmbr0","ip":"192.168.1.15/24","gateway":"192.168.1.1","vlan":"20"},{"bridge":"vmbr1","ip":"10.9.0.15/24","gateway":"","vlan":"30"}]'
    create_ct companion
)
grep -q -- '--net0 name=eth0,bridge=vmbr0,ip=192.168.1.15/24,ip6=manual,gw=192.168.1.1,tag=20 --net1 name=eth1,bridge=vmbr1,ip=10.9.0.15/24,ip6=manual,tag=30' "$calls_file"

# Solo ofrecer NDI al panel si hay una instalación comprobada en el CT esperado.
(
    ct_state() {
        if [[ "$1" == "$NDI_CT_ID" && "$scenario" == absent ]]; then printf 'new\n'
        else printf 'existing\n'; fi
    }
    verify_ct_config() {
        [[ "$1" != "$NDI_CT_ID" || "$scenario" != wrong_network ]] || die 'NIC NDI distinta.'
    }
    ndi_has_services() { [[ "$scenario" != incomplete ]]; }
    apply_native_service() { printf 'native %s\n' "$1" >> "$calls_file"; }
    pct() {
        case "$1" in
            status)
                if [[ "$2" == "$NDI_CT_ID" && "$scenario" == stopped ]]; then printf 'status: stopped\n'
                else printf 'status: running\n'; fi ;;
            exec)
                [[ "$2" == "$CADDY_CT_ID" && "$3" == -- ]] || return 99
                case "$4" in
                    test) [[ "$scenario" != panel_pending ]] ;;
                    runuser) printf 'panel:%s:%s\n' "${11}" "${12}" >> "$calls_file" ;;
                    *) return 99 ;;
                esac ;;
            *) return 99 ;;
        esac
    }
    scenario=installed
    : > "$calls_file"
    NDI_PORT=5974 apply_ndi
    [[ "$(cat "$calls_file")" == $'native ndi\n'"panel:$NDI_IP:5974" ]]
    scenario=absent
    : > "$calls_file"
    sync_panel_ndi
    [[ "$(cat "$calls_file")" == "panel::$NDI_PORT" ]]
    for scenario in stopped wrong_network incomplete panel_pending; do
        : > "$calls_file"
        sync_panel_ndi
        [[ ! -s "$calls_file" ]]
    done
)

# Reutilizar una plantilla local no debe actualizar el catálogo ni descargarla.
check_storage() { :; }
pveam() {
    if [[ "$1" == list ]]; then
        printf '%s\n' 'NAME SIZE' 'local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst 120000000' \
            'local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst 130000000'
    else
        printf 'Acción pveam no esperada: %s\n' "$*" >&2
        return 99
    fi
}
ensure_template
[[ "$TEMPLATE_VOLID" == local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst ]]

# Si falta Debian 12, seleccionar la más reciente sin tomar una Debian 13.
pveam() {
    case "$1" in
        list) printf 'NAME SIZE\n' ;;
        update) : ;;
        available) printf '%s\n' 'system debian-12-standard_12.7-1_amd64.tar.zst' \
            'system debian-12-standard_12.12-1_amd64.tar.zst' \
            'system debian-13-standard_13.0-1_amd64.tar.zst' ;;
        download) [[ "$2" == local && "$3" == debian-12-standard_12.12-1_amd64.tar.zst ]] ;;
        *) return 99 ;;
    esac
}
ensure_template
[[ "$TEMPLATE_VOLID" == local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst ]]

# Un ID ocupado debe impedir pct create, incluso después de la selección inicial.
ct_state() { printf 'existing\n'; }
pct() { printf 'No debería llamarse pct: %s\n' "$*" >&2; return 99; }
if (create_ct adguard); then
    printf 'Se ha aceptado un ID ocupado.\n' >&2
    exit 1
fi

# Sin Debian 12 en el catálogo el instalador debe fallar, sin descargar otra versión.
pveam() {
    case "$1" in
        list) printf 'NAME SIZE\n' ;;
        update) : ;;
        available) printf 'system debian-13-standard_13.0-1_amd64.tar.zst\n' ;;
        *) return 99 ;;
    esac
}
if (ensure_template); then
    printf 'Se ha aceptado un catálogo sin Debian 12.\n' >&2
    exit 1
fi

# Probar la elección del inventario sin acceder a ningún CT ni ejecutar PHP.
inventory_dir="$(mktemp -d)"
trap 'rm -f -- "$calls_file" "$inventory_dir/live-services.json"; rmdir -- "$inventory_dir"' EXIT
inventory_case() (
    local scenario="$1" target=adguard mode=live
    TEMP_DIR="$inventory_dir"
    : > "$calls_file"
    ct_state() {
        printf 'cluster\n' >> "$calls_file"
        case "$scenario" in
            cluster_error) return 1 ;;
            absent) printf 'new\n' ;;
            *) printf 'existing\n' ;;
        esac
    }
    verify_ct() {
        printf 'identity\n' >> "$calls_file"
        [[ "$scenario" != wrong_ct ]] || die 'Identidad incorrecta simulada.'
    }
    pct() {
        printf 'pct %s\n' "$*" >> "$calls_file"
        case "$1" in
            status)
                if [[ "$scenario" == stopped ]]; then printf 'status: stopped\n'; else printf 'status: running\n'; fi ;;
            exec)
                if [[ "$3" == -- && "$4" == bash ]]; then
                    case "$scenario" in
                        probe_error) return 1 ;;
                        incomplete) printf incomplete ;;
                        uninitialized) printf new ;;
                        *) printf ready ;;
                    esac
                else
                    [[ "$scenario" != export_error ]] || return 1
                    printf '[]\n'
                fi ;;
            *) return 99 ;;
        esac
    }
    python3() { printf 'render %s\n' "$*" >> "$calls_file"; }
    case "$scenario" in
        preview) mode=seed ;;
        companion|ndi) target="$scenario" ;;
    esac
    render_files "$inventory_dir/output" "$target" "$mode"
)
assert_no_log() {
    if grep -qE -- "$1" "$calls_file"; then die "Llamada inesperada en la prueba: $1"; fi
}

for scenario in preview companion ndi; do
    inventory_case "$scenario"
    assert_no_log '^(cluster|identity|pct)( |$)'
    grep -q '^render ' "$calls_file"
    assert_no_log --runtime
done
for scenario in absent uninitialized; do
    inventory_case "$scenario"
    grep -q -- "--services $ROOT/services.json" "$calls_file"
    assert_no_log --runtime
done
inventory_case live
grep -q -- "--services $inventory_dir/live-services.json" "$calls_file"
grep -q -- '--runtime' "$calls_file"
[[ "$(cat "$inventory_dir/live-services.json")" == '[]' ]]
for scenario in stopped cluster_error probe_error incomplete export_error wrong_ct; do
    if (inventory_case "$scenario"); then
        printf 'Se ha aceptado un inventario inaccesible: %s\n' "$scenario" >&2
        exit 1
    fi
    assert_no_log '^render '
done

# Ejecutar el coordinador completo con operaciones Proxmox simuladas.
# Solo se sustituyen sus efectos externos; selección, orden y flags son reales.
mkdir "$inventory_dir/scripts"
mkdir "$inventory_dir/checkout"
cp "$ROOT/install.sh" "$inventory_dir/install.sh"
cp "$ROOT/sync.sh" "$inventory_dir/sync.sh"
export FLUGE_INSTALL_ROOT="$ROOT" FLUGE_INSTALL_EVENTS="$calls_file" FLUGE_INSTALL_TEMP="$inventory_dir"
trap 'rm -f -- "$calls_file" "$inventory_dir/live-services.json" "$inventory_dir/install.sh" "$inventory_dir/sync.sh" "$inventory_dir/scripts/common.sh" "$inventory_dir/post-pve-install.sh" "$inventory_dir/config.env" "$inventory_dir/checkout/config.env" "$inventory_dir/checkout/config.env.bak"; rmdir -- "$inventory_dir/checkout" "$inventory_dir/scripts" "$inventory_dir"' EXIT
cat > "$inventory_dir/scripts/common.sh" <<'MOCK'
source "$FLUGE_INSTALL_ROOT/scripts/common.sh"
REPO_DIR="$FLUGE_INSTALL_TEMP/checkout"
init_runtime() { :; }
load_config() {
    local file="$1"
    if [[ -f "$file" ]]; then source_config "$file"; fi
}
require_proxmox() { TEMP_DIR="$FLUGE_INSTALL_TEMP"; }
verify_host_ip() { :; }
need_commands() { :; }
event() { printf '%s\n' "$*" >> "$FLUGE_INSTALL_EVENTS"; }
confirm() {
    if [[ "$ASSUME_YES" == 1 ]]; then return 0; fi
    case "$1" in
        *post-install*) return 0 ;;
        *'Guardar config.env'*) event confirmation; [[ "$FLUGE_INSTALL_SCENARIO" != cancel ]] ;;
        *) return 1 ;;
    esac
}
run_community_post_install() { event post-install; }
python3() {
    if [[ "$1" == */ndi_installer.py ]]; then
        event license
        case "$FLUGE_INSTALL_SCENARIO" in
            license_refused) [[ -z "$NDI_LICENSE_SHA256" ]] || die 'Se tomó una aceptación del entorno.'; return 1 ;;
            license_saved) [[ -z "$NDI_LICENSE_SHA256" ]] || return 99 ;;
            license_reuse|license_changed) [[ "$NDI_LICENSE_SHA256" == "$(printf '%064d' 1)" ]] || return 99 ;;
        esac
        if [[ "$FLUGE_INSTALL_SCENARIO" == license_changed ]]; then printf '%064d\n' 2; return; fi
        printf '%064d\n' 1
        return
    fi
    [[ "$1" == */questionnaire.py ]] || return 99
    event questionnaire
    printf "INSTALL_CTS='adguard companion'\n" > "$2"
}
pct() {
    [[ "$1" == status && ( "$2" == "$ADGUARD_CT_ID" || "$2" == "$COMPANION_CT_ID" ) ]] || die 'Se consultó un CT ajeno a la selección.'
    printf 'status: running\n'
}
ct_state() {
    if [[ "$FLUGE_INSTALL_SCENARIO" =~ ^(fresh|render_fail|questionnaire|cancel|license_.*)$ ]] \
        || [[ "$FLUGE_INSTALL_SCENARIO" == native && "$1" == "$COMPANION_CT_ID" ]]; then
        printf 'new\n'
    else printf 'existing\n'; fi
}
verify_ct_config() {
    event "identity $1"
    [[ "$FLUGE_INSTALL_SCENARIO" != bad_identity ]] || die 'Identidad incorrecta simulada.'
}
verify_ct() { event "verify $1"; }
start_ct() { event "start $1"; }
assert_services() { event "services $1"; }
preflight_new_ct() {
    event "preflight $1"
    if [[ "$FLUGE_INSTALL_SCENARIO" == license_* && "$1" == ndi ]]; then ndi_preflight; fi
}
check_storage() { :; }
ensure_template() {
    if [[ "$FLUGE_INSTALL_SCENARIO" == questionnaire ]]; then test -f "$REPO_DIR/config.env" || die 'Falta guardar config.env.'; fi
    event template
}
render_files() { event "render $2"; [[ "$FLUGE_INSTALL_SCENARIO" != render_fail ]]; }
create_ct() { event "create $1"; }
provision_adguard() { event 'provision adguard'; }
provision_caddy() { event 'provision caddy'; }
provision_companion() { event 'provision companion'; }
provision_ndi() { event 'provision ndi'; }
apply_adguard() { event 'apply adguard'; }
apply_caddy() { event 'apply caddy'; }
apply_companion() { event 'apply companion'; }
apply_ndi() { event 'apply ndi'; }
MOCK
installer_case() {
    : > "$calls_file"
    FLUGE_INSTALL_SCENARIO="$1" bash "$inventory_dir/install.sh" --yes "${@:2}"
}
installer_case skip
[[ ! -s "$calls_file" ]]
installer_case native
[[ "$(cat "$calls_file")" == $'preflight companion\nrender companion\ntemplate\ncreate companion\nprovision companion\napply companion' ]]
installer_case update --update-existing
[[ "$(tail -n 4 "$calls_file")" == $'apply companion\napply ndi\napply caddy\napply adguard' ]]
awk '/^identity/ {ids[$2]=1} /^start/ {if (!ids[$2]) exit 1; starts++} /^render/ {if (starts!=4) exit 1}' "$calls_file"
installer_case fresh
awk '/^render/ {rendered=1} /^create/ {if (!rendered) exit 1; count++} END {if (count!=4) exit 1}' "$calls_file"
for scenario in bad_identity render_fail; do
    if installer_case "$scenario" --update-existing; then die "Instalación aceptada: $scenario"; fi
    assert_no_log '^(create|apply|start) '
done
for scenario in cancel questionnaire; do
    : > "$calls_file"
    FLUGE_INSTALL_SCENARIO="$scenario" bash "$inventory_dir/install.sh"
    [[ "$(head -n 2 "$calls_file")" == $'post-install\nquestionnaire' ]]
    if [[ "$scenario" == cancel ]]; then
        [[ ! -e "$inventory_dir/checkout/config.env" ]]
        assert_no_log '^(create|apply|start|render) '
    else
        grep -q "^INSTALL_CTS='adguard companion'$" "$inventory_dir/checkout/config.env"
        [[ "$(sed -n 's/^create //p' "$calls_file")" == $'adguard\ncompanion' ]]
        [[ "$(sed -n 's/^apply //p' "$calls_file")" == $'companion\nadguard' ]]
    fi
done
installer_case fresh
[[ "$(sed -n 's/^create //p' "$calls_file")" == $'adguard\ncompanion' ]]
assert_no_log '^(questionnaire|post-install)$'
: > "$calls_file"
FLUGE_INSTALL_SCENARIO=sync bash "$inventory_dir/sync.sh" --yes
[[ "$(sed -n 's/^apply //p' "$calls_file")" == $'companion\nadguard' ]]
assert_no_log '^create '

# La precomprobación NDI precede a cualquier creación y persiste la licencia una vez.
if NDI_LICENSE_SHA256="$(printf '%064d' 1)" installer_case license_refused --ct ndi; then
    die 'La instalación continuó sin una licencia aceptada.'
fi
[[ "$(cat "$calls_file")" == $'preflight ndi\nlicense' ]]
installer_case license_saved --ct ndi
grep -qx "NDI_LICENSE_SHA256=$(printf '%064d' 1)" "$inventory_dir/checkout/config.env"
if grep -q '^NDI_LICENSE_SHA256=' "$inventory_dir/checkout/config.env.bak"; then
    die 'La copia de configuración anterior ya contiene la nueva aceptación.'
fi
awk '/^license$/ {accepted=1} /^create/ {if (!accepted) exit 1; created=1} END {if (!created) exit 1}' "$calls_file"
installer_case license_reuse --ct ndi
[[ "$(grep -c '^NDI_LICENSE_SHA256=' "$inventory_dir/checkout/config.env")" == 1 ]]
installer_case license_changed --ct ndi
grep -qx "NDI_LICENSE_SHA256=$(printf '%064d' 2)" "$inventory_dir/checkout/config.env"
grep -qx "NDI_LICENSE_SHA256=$(printf '%064d' 1)" "$inventory_dir/checkout/config.env.bak"
[[ "$(grep -c '^NDI_LICENSE_SHA256=' "$inventory_dir/checkout/config.env")" == 1 ]]

# Comprobar los validadores reales: un error de clúster o de identidad no debe
# convertirse en un ID libre ni permitir ejecutar un comando dentro de otro CT.
for scenario in occupied remote cluster_failure; do
    if (
        source "$ROOT/scripts/common.sh"
        pvesh() {
            case "$scenario" in
                occupied) printf '[{"vmid":110,"type":"qemu","node":"localnode"}]' ;;
                remote) printf '[{"vmid":110,"type":"lxc","node":"othernode"}]' ;;
                cluster_failure) printf '[]'; return 1 ;;
            esac
        }
        readlink() { printf '/etc/pve/nodes/localnode\n'; }
        ct_state 110
    ); then die "ID aceptado sin validación: $scenario"; fi
done
for scenario in wrong_network unreadable_config; do
    : > "$calls_file"
    if (
        source "$ROOT/scripts/common.sh"
        pct() {
            if [[ "$1" == config ]]; then
                [[ "$scenario" != unreadable_config ]] || return 1
                printf 'unprivileged: 1\nnet0: name=eth0,bridge=vmbr0,ip=192.168.99.11/24\n'
            else printf 'exec\n' >> "$calls_file"; fi
        }
        verify_ct 110 "$ADGUARD_IP"
    ); then die "CT aceptado sin identidad: $scenario"; fi
    assert_no_log '^exec$'
done
printf '[OK] Pruebas Bash de CTs, plantillas, inventario y coordinador de instalación.\n'
