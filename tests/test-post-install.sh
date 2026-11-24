#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/common.sh
source "$ROOT/scripts/common.sh"

if [[ "${1:-}" == --fixture ]]; then
    TEMP_DIR="$FLUGE_POST_TEST_DIR"
    curl() { cp -- "$TEMP_DIR/vendor.sh" "${@: -1}"; }
    sha256sum() { [[ "$2" == --status && "$FLUGE_POST_SCENARIO" != modified ]]; }
    # Invocada desde el perfil cargado por el fixture.
    # shellcheck disable=SC2317,SC2329
    disable_legacy_ceph_enterprise() {
        [[ "$1" == /etc/apt ]] || return 1
        printf 'ceph-enterprise-disabled\n' >> "$TEMP_DIR/apt.log"
        [[ "$FLUGE_POST_SCENARIO" != ceph_error ]]
    }
    # Lo llama el script comunitario simulado mediante el perfil.
    # shellcheck disable=SC2317,SC2329
    apt-get() {
        printf '%s\n' "$*" >> "$TEMP_DIR/apt.log"
        [[ "$FLUGE_POST_SCENARIO" != apt_error || "${!#}" != dist-upgrade ]]
    }
    run_community_post_install
    exit
fi

FLUGE_POST_TEST_DIR="$(mktemp -d)"
export FLUGE_POST_TEST_DIR
trap 'rm -f -- "$FLUGE_POST_TEST_DIR"/{vendor.sh,post-pve-install.sh,apt.log,finished,post-install-core/api/api.func,apt/sources.list,apt/sources.list.fluge-before-post-install,apt/sources.list.d/ceph.list,apt/sources.list.d/ceph.list.fluge-before-post-install}; rmdir -- "$FLUGE_POST_TEST_DIR"/apt/{sources.list.d,} "$FLUGE_POST_TEST_DIR"/post-install-core/{api,} "$FLUGE_POST_TEST_DIR"' EXIT
mkdir -p "$FLUGE_POST_TEST_DIR/apt/sources.list.d"
cat > "$FLUGE_POST_TEST_DIR/apt/sources.list" <<'SOURCES'
deb https://enterprise.proxmox.com/debian/ceph-reef bookworm enterprise
deb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise
SOURCES
cat > "$FLUGE_POST_TEST_DIR/apt/sources.list.d/ceph.list" <<'SOURCES'
deb [signed-by=/usr/share/keyrings/proxmox.gpg] https://enterprise.proxmox.com/debian/ceph-squid trixie enterprise
deb http://download.proxmox.com/debian/ceph-squid trixie no-subscription
# deb https://enterprise.proxmox.com/debian/ceph-quincy bookworm enterprise
deb http://example.org/debian bookworm main # https://enterprise.proxmox.com/debian/ceph-squid
SOURCES
disable_legacy_ceph_enterprise "$FLUGE_POST_TEST_DIR/apt"
[[ "$(head -n 1 "$FLUGE_POST_TEST_DIR/apt/sources.list")" == '# deb https://enterprise.proxmox.com/debian/ceph-reef bookworm enterprise' ]]
[[ "$(tail -n 1 "$FLUGE_POST_TEST_DIR/apt/sources.list")" == 'deb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise' ]]
[[ "$(head -n 1 "$FLUGE_POST_TEST_DIR/apt/sources.list.d/ceph.list")" == '# deb [signed-by=/usr/share/keyrings/proxmox.gpg] https://enterprise.proxmox.com/debian/ceph-squid trixie enterprise' ]]
[[ "$(tail -n 3 "$FLUGE_POST_TEST_DIR/apt/sources.list.d/ceph.list")" == $'deb http://download.proxmox.com/debian/ceph-squid trixie no-subscription\n# deb https://enterprise.proxmox.com/debian/ceph-quincy bookworm enterprise\ndeb http://example.org/debian bookworm main # https://enterprise.proxmox.com/debian/ceph-squid' ]]
disable_legacy_ceph_enterprise "$FLUGE_POST_TEST_DIR/apt"
[[ "$(head -n 1 "$FLUGE_POST_TEST_DIR/apt/sources.list.fluge-before-post-install")" == 'deb https://enterprise.proxmox.com/debian/ceph-reef bookworm enterprise' ]]
cat > "$FLUGE_POST_TEST_DIR/vendor.sh" <<'MOCK'
read -r initial
[[ "$initial" == y ]]
menu() {
    local title="$1" prompt="$2"
    shift 2
    whiptail --backtitle Test --title "$title" --menu "$prompt" 14 58 3 "$@" 3>&2 2>&1 1>&3
}
[[ -f "${COMMUNITY_SCRIPTS_CORE_URL#file://}/api/api.func" ]]
[[ "$(menu SOURCES 'Correct sources?' yes '' no '')" == no ]]
[[ "$(menu PVE-ENTERPRISE 'Disable repository?' yes '' no '')" == yes ]]
[[ "$(menu PVE-ENTERPRISE 'Add repository?' no '' yes '' --default-item no)" == no ]]
[[ "$(menu PVE-ENTERPRISE 'Existing repository?' keep '' disable '' delete '')" == disable ]]
[[ "$(menu CEPH-ENTERPRISE 'Existing repository?' keep '' disable '' delete '')" == disable ]]
[[ "$(menu 'CEPH PACKAGE REPOSITORIES' 'Correct Ceph sources?' yes '' no '')" == no ]]
[[ "$(menu 'CEPH PACKAGE REPOSITORIES' 'Add Ceph sources?' yes '' no '')" == no ]]
[[ "$(menu PVE-NO-SUBSCRIPTION 'Add source?' yes '' no '')" == yes ]]
[[ "$(menu PVE-NO-SUBSCRIPTION 'Enable source?' enable '' keep '' delete '')" == enable ]]
[[ "$(menu PVE-NO-SUBSCRIPTION 'Enabled source?' keep '' disable '' delete '')" == keep ]]
[[ "$(menu PVETEST 'Add pvetest?' yes '' no '')" == no ]]
[[ "$(menu 'SUBSCRIPTION NAG' 'Disable nag?' yes '' no '')" == yes ]]
[[ "$(menu 'HIGH AVAILABILITY' 'Enable HA?' yes '' no '')" == no ]]
[[ "$(menu 'HIGH AVAILABILITY' 'Disable HA?' yes '' no '')" == no ]]
[[ "$(menu COROSYNC 'Disable Corosync?' yes '' no '')" == no ]]
[[ "$(menu UPDATE 'Update Proxmox?' yes '' no '')" == yes ]]
[[ "$(menu REBOOT 'Reboot now?' yes '' no '')" == no ]]
if whiptail --title 'Disable legacy sources?' --yesno 'Disable?' 18 80; then exit 5; fi
whiptail --msgbox --title 'Support Subscriptions' 'Information' 10 58
whiptail --title 'Post-Install Reminder' --msgbox 'Information' 10 58
case "$FLUGE_POST_SCENARIO" in
    reboot) reboot ;;
    unknown) choice="$(menu UNKNOWN 'Unexpected?' yes '' no '')"; printf '%s\n' "$choice" ;;
esac
apt --reinstall install proxmox-widget-toolkit || printf 'Unexpected APT failure ignored'
apt update
apt -y dist-upgrade || printf 'Unexpected APT failure ignored'
printf 'finished\n' > "$TEMP_DIR/finished"
MOCK

FLUGE_POST_SCENARIO=normal bash "$0" --fixture
[[ -f "$FLUGE_POST_TEST_DIR/finished" ]]
[[ "$(cat "$FLUGE_POST_TEST_DIR/apt.log")" == $'ceph-enterprise-disabled\n-o APT::Update::Error-Mode=any update\n-y --reinstall install proxmox-widget-toolkit\n-y -y dist-upgrade' ]]
for scenario in reboot unknown modified apt_error ceph_error; do
    rm -f -- "$FLUGE_POST_TEST_DIR/finished"
    if FLUGE_POST_SCENARIO="$scenario" bash "$0" --fixture; then
        die "El perfil ha completado una operación que debía abortar: $scenario"
    fi
    [[ ! -e "$FLUGE_POST_TEST_DIR/finished" ]]
done
printf '[OK] Perfil del post-install, respuestas y bloqueo de reinicio.\n'
