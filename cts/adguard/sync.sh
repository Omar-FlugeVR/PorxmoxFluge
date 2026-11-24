#!/usr/bin/env bash
# Sincroniza únicamente AdGuard desde el HOST Proxmox.
set -euo pipefail
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
exec bash "$REPO_DIR/sync.sh" "$@" --ct adguard
