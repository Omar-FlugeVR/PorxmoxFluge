#!/usr/bin/env bash
# Publica el panel nativo y las rutas actuales. El estado privado nunca se reemplaza.
set -euo pipefail
stage="$1"
[[ "$stage" == /tmp/studio-caddy.* && -d "$stage" ]] || { printf 'Staging inválido.\n' >&2; exit 1; }
install -d -m 0700 /run/fluge-control
backup=''
changed=0
was_active=0
had_config=0
had_routes=0
had_app=0
rollback() {
    local status="$?"
    trap - EXIT
    if [[ "$status" != 0 && "$changed" == 1 ]]; then
        printf '[ERROR] Restaurando Caddy desde %s\n' "$backup" >&2
        if [[ "$had_config" == 1 ]]; then cp -p -- "$backup/Caddyfile" /etc/caddy/Caddyfile; else rm -f -- /etc/caddy/Caddyfile; fi
        if [[ "$had_routes" == 1 ]]; then cp -p -- "$backup/routes.caddy" /etc/caddy/studio-routes.caddy; else rm -f -- /etc/caddy/studio-routes.caddy; fi
        if [[ "$had_app" == 1 ]]; then
            if [[ -d /opt/fluge-control ]]; then mv -- /opt/fluge-control "$stage/failed-panel"; fi
            mv -- "$backup/panel" /opt/fluge-control
        elif [[ -d /opt/fluge-control ]]; then
            mv -- /opt/fluge-control "$stage/failed-panel"
        fi
        systemctl reload php8.2-fpm || true
        if [[ "$was_active" == 1 ]]; then
            systemctl reload caddy || systemctl restart caddy || printf '[ERROR] Revisa journalctl -u caddy.\n' >&2
        else systemctl stop caddy || true; fi
    fi
    rm -rf -- "$stage"
    exit "$status"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -- "$stage/panel"
tar -xf "$stage/panel.tar" -C "$stage/panel" --no-same-owner
find "$stage/panel" -type d -exec chmod 0755 {} +
find "$stage/panel" -type f -exec chmod 0644 {} +
# El código temporal es legible, pero todos los JSON con credenciales siguen en 0600.
chmod 0755 "$stage"
python3 - "$stage" <<'PY' | runuser -u www-data -- php "$stage/panel/bin/initialize.php" /var/lib/fluge-control --stdin
import json, pathlib, sys
stage = pathlib.Path(sys.argv[1])
print(json.dumps([json.loads((stage / name).read_text()) for name in ('seed.json', 'config.json', 'auth.json')]))
PY
# No mantener rutas bloqueadas al esperar al inventario: evita invertir los bloqueos de la API.
exec 8>/run/fluge-control/routes.lock
flock -x 8
runuser -u www-data -- php "$stage/panel/bin/export.php" routes > "$stage/routes.caddy"
if cmp -s "$stage/Caddyfile" /etc/caddy/Caddyfile && cmp -s "$stage/routes.caddy" /etc/caddy/studio-routes.caddy \
    && diff -qr "$stage/panel" /opt/fluge-control >/dev/null 2>&1; then
    if ! systemctl is-active --quiet caddy; then systemctl start caddy; fi
    printf '[OK] Panel y Caddy sin cambios.\n'
    exit 0
fi
install -d -m 0700 /var/backups/studio-infra/caddy
backup="$(mktemp -d /var/backups/studio-infra/caddy/change-XXXXXXXX)"
if [[ -e /etc/caddy/Caddyfile ]]; then cp -p -- /etc/caddy/Caddyfile "$backup/Caddyfile"; had_config=1; fi
if [[ -e /etc/caddy/studio-routes.caddy ]]; then cp -p -- /etc/caddy/studio-routes.caddy "$backup/routes.caddy"; had_routes=1; fi
if systemctl is-active --quiet caddy; then was_active=1; fi
install -d -m 0755 /etc/caddy /opt
changed=1
if [[ -d /opt/fluge-control ]]; then mv -- /opt/fluge-control "$backup/panel"; had_app=1; fi
mv -- "$stage/panel" /opt/fluge-control
chown -R root:root /opt/fluge-control
python3 - "$stage/config.json" <<'PY'
import json, pathlib, sys
config = json.loads(pathlib.Path(sys.argv[1]).read_text())
pathlib.Path('/etc/fluge-control/site.json').write_text(json.dumps({'dashboardDomain': config['dashboardDomain']}))
PY
chmod 0644 /etc/fluge-control/site.json
install -m 0755 /opt/fluge-control/deploy/apply-routes.py /usr/local/sbin/fluge-control-apply-routes
printf '%s\n' 'www-data ALL=(root) NOPASSWD: /usr/local/sbin/fluge-control-apply-routes ""' > "$stage/sudoers"
visudo -cf "$stage/sudoers"
install -m 0440 "$stage/sudoers" /etc/sudoers.d/fluge-control
install -m 0644 -o root -g caddy "$stage/Caddyfile" /etc/caddy/.Caddyfile.studio-new
mv -f -- /etc/caddy/.Caddyfile.studio-new /etc/caddy/Caddyfile
install -m 0644 -o root -g caddy "$stage/routes.caddy" /etc/caddy/.studio-routes.new
mv -f -- /etc/caddy/.studio-routes.new /etc/caddy/studio-routes.caddy
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl reload php8.2-fpm
if [[ "$was_active" == 1 ]]; then systemctl reload caddy; else systemctl start caddy; fi
systemctl is-active --quiet caddy
printf '[OK] Panel publicado. Copia anterior: %s\n' "$backup"
