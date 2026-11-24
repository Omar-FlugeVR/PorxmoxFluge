#!/usr/bin/env bash
# Solo se ejecuta dentro de un CT NUEVO, mediante pct exec desde Proxmox.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
timezone="$1"
build="$2"
[[ ! -e /opt/companion && ! -e /usr/local/src/companionpi ]] || {
    printf 'Companion ya existe; no se reinstala.\n' >&2
    exit 1
}
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl python3 sudo udev iproute2 tzdata
ln -snf "/usr/share/zoneinfo/$timezone" /etc/localtime
printf '%s\n' "$timezone" > /etc/timezone
stage="$(mktemp -d)"
trap 'rm -rf -- "$stage"' EXIT
curl --fail --silent --show-error --location --retry 3 \
    https://raw.githubusercontent.com/bitfocus/companion-pi/main/install.sh --output "$stage/install.sh"
# El instalador oficial obtiene el build headless y su runtime Node incluido.
# No se compila Companion, no se instala Node globalmente y no se usa Docker.
COMPANION_BUILD="$build" bash "$stage/install.sh"
test -f /opt/companion/main.js
test -x /usr/local/src/companionpi/launch.sh
systemctl enable companion
