#!/usr/bin/env bash
# Dependencias nativas y pool PHP exclusivo del panel. Se ejecuta como root en el CT.
set -euo pipefail
native_panel_dependencies_ready() {
    local package status
    for package in python3 ca-certificates curl libavahi-client3 libavahi-common3 libstdc++6 libgcc-s1; do
        status="$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null)" || return 1
        [[ "$status" == 'install ok installed' ]] || return 1
    done
}
if ! command -v php >/dev/null || ! test -x /usr/sbin/php-fpm8.2 || ! command -v sudo >/dev/null || ! command -v python3 >/dev/null \
    || ! native_panel_dependencies_ready \
    || ! php -r 'exit(extension_loaded("curl") ? 0 : 1);'; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends php8.2-cli php8.2-fpm php8.2-curl python3 sudo \
        ca-certificates curl libavahi-client3 libavahi-common3 libstdc++6 libgcc-s1
fi
install -d -m 0700 -o www-data -g www-data /var/lib/fluge-control
install -d -m 0755 -o root -g root /etc/fluge-control
pool="$(mktemp)"
trap 'rm -f -- "$pool"' EXIT
cat > "$pool" <<'POOL'
[fluge-control]
user = www-data
group = www-data
listen = /run/php/fluge-control.sock
listen.owner = caddy
listen.group = caddy
listen.mode = 0660
pm = ondemand
pm.max_children = 3
pm.process_idle_timeout = 20s
clear_env = yes
env[FLUGE_STATE_DIR] = /var/lib/fluge-control
env[FLUGE_DEMO] = 0
env[NDI_MOCK] = "false"
env[FLUGE_PYTHON] = /usr/bin/python3
php_admin_value[memory_limit] = 128M
php_admin_flag[display_errors] = off
php_admin_flag[log_errors] = on
security.limit_extensions = .php
POOL
if ! cmp -s "$pool" /etc/php/8.2/fpm/pool.d/fluge-control.conf; then
    install -m 0644 "$pool" /etc/php/8.2/fpm/pool.d/fluge-control.conf
    php-fpm8.2 -t
    systemctl restart php8.2-fpm
fi
systemctl enable --now php8.2-fpm
