# CT Bitfocus Companion

Instala Companion headless con el [instalador oficial para Debian de Bitfocus](https://github.com/bitfocus/companion-pi), sin Docker ni compilación. El paquete incluye su propio runtime Node y la unidad `companion.service`.

Valores iniciales: **CT 112**, IP `192.168.1.15`, Debian 12 unprivileged, 2 vCPU, 2048 MiB RAM, disco de 8 GiB, `onboot=1`, orden 3.

Desde el **host Proxmox**:

```bash
./cts/companion/install.sh
./cts/companion/sync.sh
./cts/companion/sync.sh --render-only
```

Configura `COMPANION_CT_ID`, `COMPANION_IP`, `COMPANION_HOSTNAME`, `COMPANION_PORT` y `COMPANION_BUILD` en el `config.env` de la raíz. El inventario usa `http://${COMPANION_IP}:${COMPANION_PORT}` para el atajo `companion.lan`. `COMPANION_BUILD=stable` instala la última estable disponible; también puedes fijar una versión estable `x.y.z` al crear el CT.

La sincronización aplica únicamente el drop-in `/etc/systemd/system/companion.service.d/10-studio-infra.conf`, que fija escucha IPv4 y puerto HTTP, y comprueba que la web responda. **Conserva el perfil, los botones y las conexiones** del usuario `companion`. No actualiza el programa ni cambia el directorio del perfil.

Si ya dispones de un Companion compatible dentro del CT seleccionado, el instalador ofrece actualizar solo su configuración. Para actualizar su versión usa de forma explícita la herramienta del fabricante, por ejemplo `pct exec 112 -- companion-update stable`, después de exportar el perfil desde la web y hacer un backup del CT. Cambiar `COMPANION_BUILD` no reinstala CTs existentes.

El puerto web por defecto es `8000/TCP`. Companion Satellite usa sus propios puertos (normalmente `16622/TCP` y `16623/TCP`); permite en el firewall los que necesites. No se configura passthrough USB: para Stream Decks conectados a otro equipo puedes usar Companion Satellite.

Archivos:

- `host.sh`: recursos, comprobaciones y operaciones desde Proxmox.
- `provision/install.sh`: instalación oficial nativa en un CT nuevo.
- `generate-config.py` y `templates/override.conf`: drop-in systemd y plan de comprobación.
- `install.sh` / `sync.sh`: entradas individuales; los archivos generados quedan en `generated/companion/`.

Las copias de configuración se guardan en `/var/backups/studio-infra/companion/`. Ante un fallo de arranque o respuesta HTTP se restauran los archivos anteriores. Revisa `pct exec 112 -- journalctl -u companion -n 50 --no-pager` para diagnosticar.

Consulta la [guía general](../../README.md). Al cambiar el inventario ejecuta `./sync.sh` desde la raíz para coordinar los cuatro CTs.
