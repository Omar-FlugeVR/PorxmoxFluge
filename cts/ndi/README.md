# CT NDI Discovery Service

Instala el servidor oficial `ndi-discovery-server` Linux x86_64 incluido en el SDK de NDI, con unidad `ndi-discovery.service`, usuario de sistema `ndi` y configuración nativa. No transporta ni transcodifica el vídeo.

Valores iniciales: **CT 113**, IP `192.168.1.16`, Debian 12 unprivileged, 1 vCPU, 512 MiB RAM, disco de 4 GiB, `onboot=1`, orden 4. Usa `5959/TCP` de forma predeterminada.

Desde una terminal interactiva del **host Proxmox**:

```bash
./cts/ndi/install.sh
./cts/ndi/sync.sh
./cts/ndi/sync.sh --render-only
```

Configura `NDI_CT_ID`, `NDI_IP`, `NDI_HOSTNAME` y `NDI_PORT` en el `config.env` de la raíz. `NDI_INSTALLER_URL` apunta al SDK Linux oficial de NDI. El host Proxmox obtiene el autoextraíble `.sh` y envía al CT la copia que se ha revisado.

Al seleccionar un CT de NDI nuevo, el cuestionario muestra el acuerdo completo al principio. Si lo aceptas, se guarda la huella SHA-256 del texto en `NDI_LICENSE_SHA256` dentro del `config.env` privado al confirmar el plan. Después, el instalador responde al fabricante automáticamente dentro del CT. Si rechazas el acuerdo, la instalación se cancela antes de crear CTs.

Con `--yes` puedes reinstalar sin terminal cuando el texto del paquete coincide con la huella guardada. Si la licencia es nueva o ha cambiado, ejecuta el instalador desde una terminal interactiva para revisarla. `--from-config` también permite guardar la aceptación en un `config.env` existente.

Si la descarga directa no está disponible, obtén el paquete oficial desde la [página de NDI](https://ndi.video/form-discovery-service/) o el [SDK](https://ndi.video/for-developers/ndi-sdk/), cópialo al host y establece, por ejemplo:

```bash
NDI_INSTALLER_FILE=/root/Install_NDI_SDK_v6_Linux.tar.gz
```

También se admite el `.sh` oficial. El formato esperado incluye `bin/x86_64-linux-gnu/ndi-discovery-server`; un instalador con una estructura diferente se rechaza con un error. Opcionalmente fija `NDI_INSTALLER_SHA256` con un hash verificado por ti; se comprueba antes de ejecutar el archivo. No se incluyen binarios ni paquetes propietarios en Git.

La sincronización genera y aplica:

- `/etc/systemd/system/ndi-discovery.service`, con `-service` y su configuración JSON.
- `/etc/ndi/ndi-discovery-service.v1.json`, con `binding` igual a `NDI_IP` y `port_no` igual a `NDI_PORT`.

El binario está en `/opt/ndi-discovery/bin/ndi-discovery-server`. Las librerías del paquete que necesita se conservan dentro de `/opt/ndi-discovery/lib`. Solo se modifica la configuración y la unidad durante `sync.sh`; no se reinstala ni se actualiza el SDK. El puerto TCP se comprueba después del arranque y se restaura la configuración anterior si falla.

En AdGuard, **`ndi.lan` resuelve directamente a la IP de este CT**. No tiene sitio de Caddy ni redirección HTTP. Configura los emisores y receptores en NDI Access Manager con `NDI_IP` (o el nombre DNS si la aplicación lo admite) y permite `NDI_PORT/TCP`. Publicar ese DNS no configura los clientes ni las rutas o los puertos de transporte de vídeo entre ellos.

Las copias se guardan en `/var/backups/studio-infra/ndi-discovery/`. Diagnóstico:

```bash
pct exec 113 -- systemctl status ndi-discovery --no-pager
pct exec 113 -- journalctl -u ndi-discovery -n 50 --no-pager
```

Referencias: [opciones oficiales de Discovery Service](https://docs.ndi.video/all/developing-with-ndi/sdk/command-line-tools) y [instalación Linux y unidad systemd](https://docs.ndi.video/all/using-ndi/ndi-tools/ndi-tools-for-windows/discovery/discovery-server-additional-information). Consulta también la [guía general](../../README.md).
