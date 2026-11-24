# CT AdGuard Home

DNS local del estudio en un LXC Debian 12 unprivileged: 1 vCPU, 512 MiB RAM, disco de 4 GiB, arranque automático con orden 1. Escucha en `53/UDP`, `53/TCP` y `80/TCP` para administración web.

Ejecuta desde el **host Proxmox**, usando el `config.env` y `services.json` de la raíz:

```bash
./cts/adguard/install.sh
./cts/adguard/sync.sh
./cts/adguard/sync.sh --render-only
```

Estos comandos actúan solo sobre AdGuard. También puedes usar `./install.sh --ct adguard` y `./sync.sh --ct adguard`. Admiten las mismas opciones que los comandos generales, incluido `--yes` y, al instalar, `--update-existing`.

La previsualización se escribe en `generated/adguard/rewrites.json`. El plan publica el wildcard del dominio hacia Caddy y los nombres `dns_only` hacia su IP directa. Caddy debe estar instalado para que los atajos HTTP y la landing funcionen.

Archivos de este módulo:

- `host.sh`: recursos, comprobaciones, aprovisionamiento y transferencia desde el host.
- `generate-rewrites.py`: generación del plan DNS a partir del inventario.
- `provision/install.sh`: instalación oficial nativa y configuración inicial dentro del CT.
- `provision/apply.py`: actualización de rewrites con validación, copias y restauración.

Los valores `ADGUARD_CT_ID`, `ADGUARD_IP`, `ADGUARD_HOSTNAME`, `ADGUARD_ADMIN_USER` y `UPSTREAM_DNS` se mantienen en `config.env`. Las credenciales iniciales se guardan en `.secrets/adguard-admin.json`.

Si cambias el inventario que usan AdGuard y Caddy, ejecuta `./sync.sh` desde la raíz para actualizar Caddy y DNS de forma coordinada. Consulta la [guía general](../../README.md) para requisitos, DNS de clientes y recuperación.
