#!/usr/bin/env bash
# Punto de entrada desde el HOST Proxmox, no desde dentro del CT.
set -euo pipefail
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
exec bash "$REPO_DIR/install.sh" "$@" --ct caddy
