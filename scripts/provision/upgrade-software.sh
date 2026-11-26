#!/usr/bin/env bash
# Called by the Proxmox update worker in an already verified CT.
set -euo pipefail
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
role="$1" version="$2" host="$3" port="$4" stage="${5:-}"
case "$role" in
    adguard) unit=AdGuardHome.service; app=/opt/AdGuardHome ;;
    companion) unit=companion.service; app=/opt/companion ;;
    ndi) unit=ndi-discovery.service; app=/opt/ndi-discovery ;;
    caddy) unit=caddy.service; app=/etc/caddy ;;
    *) exit 2 ;;
esac
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
systemctl is-active --quiet "$unit"
backup_root="/var/backups/studio-infra/$role"
install -d -m 0700 "$backup_root"
backup="$(mktemp -d "$backup_root/update-XXXXXXXX")"
stopped=0
modified=0
restore() {
    local status="$?"
    trap - EXIT
    if [[ "$status" != 0 && "$stopped" == 1 ]]; then
        set +e
        printf '[ERROR] Recuperando %s desde %s\n' "$role" "$backup" >&2
        if [[ "$modified" == 1 ]]; then
            systemctl stop "$unit"
            if [[ "$role" == caddy ]]; then
                apt-get install -y --allow-downgrades "$backup/caddy.deb"
                cp -a -- "$backup/app/." "$app/"
            else
                if [[ -e "$app" ]]; then mv -- "$app" "$backup/failed-app"; fi
                mv -- "$backup/app" "$app"
                if [[ "$role" == companion ]]; then
                    if [[ -e /home/companion ]]; then mv -- /home/companion "$backup/failed-profile"; fi
                    mv -- "$backup/profile" /home/companion
                    mv -- /usr/local/src/companionpi "$backup/failed-updater"
                    mv -- "$backup/updater" /usr/local/src/companionpi
                    if [[ -f "$backup/companion.service" ]]; then
                        cp -a -- "$backup/companion.service" /etc/systemd/system/companion.service
                        systemctl daemon-reload
                    fi
                fi
            fi
        fi
        systemctl start "$unit"
    fi
    if [[ "$role" == ndi && "$stage" == /root/fluge-ndi-upgrade.* ]]; then rm -rf -- "$stage"; fi
    exit "$status"
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ "$role" == caddy ]]; then
    apt-get -o Acquire::Retries=1 update
    candidate="$(apt-cache policy caddy | awk '$1=="Candidate:" {print $2}')"
    candidate_version="${candidate#*:}"
    [[ "$candidate_version" == "$version" || "$candidate_version" == "$version"-* ]] || {
        printf 'La versión %s aún no es candidata en APT. Candidata: %s\n' "$version" "$candidate" >&2; exit 1;
    }
    old_version="$(dpkg-query -W -f='${Version}' caddy)"
    # Descargar antes de parar Caddy, incluida la versión que permite recuperar APT.
    (cd -- "$backup"; apt-get download "caddy=$old_version" >/dev/null; mv -- caddy_*.deb caddy.deb)
    apt-get install -y --download-only --only-upgrade "caddy=$candidate"
elif [[ "$role" == companion ]]; then
    cp -a --reflink=auto -- /usr/local/src/companionpi "$backup/updater"
elif [[ "$role" == ndi ]]; then
    [[ "$stage" == /root/fluge-ndi-upgrade.* && -f "$stage/ndi-discovery-server" && -f "$stage/libndi.so.$version" ]]
fi

stopped=1
systemctl stop "$unit"
cp -a --reflink=auto -- "$app" "$backup/app"
if [[ "$role" == companion ]]; then
    cp -a --reflink=auto -- /home/companion "$backup/profile"
    cp -a -- /etc/systemd/system/companion.service "$backup/companion.service"
fi
modified=1
case "$role" in
    adguard)
        cd -- "$app"
        ./AdGuardHome --update -v -c "$app/AdGuardHome.yaml" -w "$app"
        ./AdGuardHome --check-config -c "$app/AdGuardHome.yaml" -w "$app"
        installed="$(./AdGuardHome --version)"
        ;;
    companion)
        git -C /usr/local/src/companionpi pull --ff-only https://github.com/bitfocus/companion-pi.git main
        cd /usr/local/src/companionpi
        bash ./update.sh stable "$version"
        installed="$(cat /opt/companion/BUILD)"
        ;;
    ndi)
        install -m 0755 "$stage/ndi-discovery-server" "$app/bin/ndi-discovery-server"
        install -m 0644 "$stage/libndi.so.$version" "$app/lib/libndi.so.$version"
        ln -sfn -- "libndi.so.$version" "$app/lib/libndi.so.6"
        ln -sfn -- libndi.so.6 "$app/lib/libndi.so"
        install -m 0644 "$stage/installer-license-header.txt" "$app/installer-license-header.txt"
        dependencies="$(LD_LIBRARY_PATH="$app/lib" ldd "$app/bin/ndi-discovery-server")"
        [[ "$dependencies" != *'not found'* ]]
        installed="$version"
        ;;
    caddy)
        apt-get install -y --only-upgrade "caddy=$candidate"
        caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
        installed="$(caddy version)"
        ;;
esac
version_pattern="${version//./[.]}"
[[ "$installed" =~ (^|[[:space:]])v?${version_pattern}([[:space:]+-]|$) ]] || {
    printf 'La versión instalada no coincide con %s: %s\n' "$version" "$installed" >&2; exit 1;
}
systemctl start "$unit"
python3 - "$host" "$port" <<'PY'
import socket, sys, time
for _ in range(20):
    try:
        with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=1):
            break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit('El servicio actualizado no responde en su puerto configurado.')
PY
systemctl is-active --quiet "$unit"
printf '[OK] %s actualizado. Copia anterior: %s\n' "$role" "$backup"
