#!/usr/bin/env bash
# Instalación nativa dentro del CT NUEVO de Caddy, mediante pct exec.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
timezone="$1"
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    debian-keyring debian-archive-keyring apt-transport-https tzdata
ln -snf "/usr/share/zoneinfo/$timezone" /etc/localtime
printf '%s\n' "$timezone" > /etc/timezone
stage="$(mktemp -d)"
trap 'rm -rf -- "$stage"' EXIT
curl --fail --silent --show-error --location --retry 3 \
    https://dl.cloudsmith.io/public/caddy/stable/gpg.key --output "$stage/caddy.key"
gpg --batch --yes --dearmor --output /usr/share/keyrings/caddy-stable-archive-keyring.gpg "$stage/caddy.key"
curl --fail --silent --show-error --location --retry 3 \
    https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt --output "$stage/caddy.list"
install -m 0644 "$stage/caddy.list" /etc/apt/sources.list.d/caddy-stable.list
chmod 0644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg
apt-get update
apt-get install -y --no-install-recommends caddy
install -d -m 0755 /var/www/landing
systemctl enable --now caddy
