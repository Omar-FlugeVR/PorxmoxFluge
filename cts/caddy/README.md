# CT Caddy y Servicio de control

Caddy, PHP 8.2-FPM y el panel Fluge se instalan directamente dentro del mismo LXC Debian 12 unprivileged: 1 vCPU, 512 MiB RAM, disco de 4 GiB, arranque automático con orden 2. Los accesos web utilizan redirecciones HTTP `302`; los equipos reciben las conexiones del navegador directamente.

Desde el **host Proxmox**:

```bash
./cts/caddy/install.sh
./cts/caddy/sync.sh
./cts/caddy/sync.sh --render-only
```

También puedes usar `./install.sh --ct caddy` y `./sync.sh --ct caddy`. Un CT preexistente necesita Caddy ya instalado; al publicar el panel se instalan sus dependencias PHP-FPM, cURL, Python y sudo si faltan. No se cambia automáticamente su memoria asignada. Mantén al menos 512 MiB para el panel nuevo.

## Probar en local

```bash
python scripts/preview-panel.py --php /ruta/php
```

Desde la raíz del repositorio, abre **http://127.0.0.1:8787/** con **admin / FlugeDemo2026**. Solo escucha en loopback. Escaneo, publicación DNS y recargas Caddy están simulados; el inventario de la muestra se guarda en `generated/demo/`. Las pruebas HTTP usan otro directorio temporal independiente.

## Uso

La cabecera conserva el logo vectorial blanco de Fluge y «Servicio de control», sobre el fondo geométrico oscuro. No utiliza fuentes, frameworks ni recursos remotos.

El favicon de la pestaña usa únicamente el símbolo vectorial de Fluge, sin el texto del logotipo.

En **Configuración → Instalación** puedes guardar el nombre de la instalación. Se muestra debajo de «Servicio de control», en un bloque de la misma altura y anchura de contenido que el logo, con tamaños adaptados a móvil. Se comparte con todos los usuarios y persiste en la configuración privada; los nombres largos se recortan visualmente y conservan el texto completo en su atributo de título. Dejar el campo vacío oculta la segunda línea.

- **Servicios:** accesos web con redirección 302 o servicios solo DNS, como NDI. Categorías, buscador y edición desde tarjetas.
- **Equipos:** libreta de IP, MAC, notas y comandos SSH. Un equipo puede tener varios servicios.
- **Escaneo:** redes privadas permitidas, progreso, cancelación, equipos nuevos/registrados y registro de lo descubierto. «No activos» muestra las IP comprobadas sin respuesta del último escaneo, con los equipos registrados primero. Las IP fuera del rango, las peticiones canceladas y las direcciones aún sin comprobar quedan sin evaluar. Incluye Companion y NDI en la lista de puertos TCP.
- **Configuración:** categorías compartidas, conexión con AdGuard, redes con nombre y pool DHCP propio, cuentas locales, importación/exportación y reintento de publicación.

En Categorías puedes añadir, renombrar y eliminar. Renombrar actualiza todas las referencias de servicios y equipos, los filtros y los formularios. Al eliminar una categoría con elementos, elige otra para trasladarlos; no se eliminan equipos ni servicios. Los cambios se guardan y aplican automáticamente como una sola revisión del inventario. Se conserva al menos una categoría y se rechazan nombres duplicados y cambios de sesiones con revisiones antiguas.

El único botón «Guardar» persiste los cambios y publica automáticamente primero Caddy y después DNS, dentro de la misma petición al servidor. Las ediciones, borrados, importaciones y ajustes usan el mismo comportamiento. Si falla alguna parte, los datos siguen guardados y se muestra el error; «Reintentar actualización» aparece únicamente cuando no se ha podido confirmar la publicación. Configuración muestra el estado de los accesos sin exigir un paso de aplicación manual. El borrado de un equipo de la libreta conserva sus servicios; se eliminan desde Servicios. El escaneo distingue equipos registrados por su IP y no configura las NIC ni el router.

En **Redes y escaneo** puedes añadir, editar y eliminar hasta 32 redes privadas con nombre, CIDR y pool DHCP opcional. Cada una aparece como preset en Escaneo; seleccionarla rellena el rango y muestra su pool, sin iniciar la búsqueda. Dejar ambos extremos del pool vacíos indica una red sin DHCP. Se conserva al menos una red y se rechazan nombres, identificadores y CIDR duplicados. Los pools deben quedar dentro de las direcciones utilizables de su red. La configuración anterior se convierte en redes iniciales, conservando los nombres de los presets y la parte del intervalo DHCP que corresponda a cada red.

Escaneo selecciona **Todas las redes** al entrar: el campo contiene sus CIDR separados por ` ; `, y **Iniciar escaneo** los comprueba juntos. También puedes volver a seleccionar todas con ese botón, elegir un preset individual o introducir rangos manuales separados por `;`. Se conserva el límite de 65.536 direcciones únicas por escaneo y no se repiten las IP de redes solapadas.

El filtro **DHCP** muestra los equipos activos dentro del pool de su propia red, también cuando se busca manualmente en varias redes. Si hay subredes solapadas, se usa la más específica; una subred sin pool no hereda el de otra más amplia. No consulta las concesiones del router ni modifica su DHCP. «Sin respuesta» se refiere a las comprobaciones del escáner; no demuestra que un equipo esté apagado. El inventario conserva sus fichas al cambiar de rango y no asigna estados a direcciones que no se hayan comprobado.

En **Cuentas**, cada usuario tiene su propia fila y la sesión actual aparece resaltada. La cuenta principal no se puede eliminar, desactivar ni degradar. Solo ella ve y puede usar **Cambiar contraseña** en las filas de todos los usuarios, incluida la suya; no necesita introducir la contraseña anterior. Las demás cuentas, incluso administradoras, no pueden cambiar contraseñas. Tanto la creación como el cambio requieren introducir dos veces la nueva contraseña, con coincidencia validada en el navegador y en el servidor. Los cambios revocan las sesiones anteriores de la cuenta afectada; al cambiar su propia contraseña, la cuenta principal conserva únicamente la sesión desde la que lo hizo. Los cambios de contraseñas requieren CSRF y una revisión vigente de la lista de usuarios. Las antiguas cuentas vinculadas pasan a tener credenciales independientes del panel al cambiar su contraseña, sin modificar las credenciales de integración con AdGuard. **Copia del inventario** contiene únicamente las acciones de importar y exportar el inventario.

## Monitorización NDI (opcional)

El módulo empieza desactivado y se configura en **Configuración → NDI Discovery Server**. Sus ajustes se guardan en la clave `ndi` del mismo `/var/lib/fluge-control/config.json` que usa el panel:

```json
{"enabled":true,"ip":"192.168.10.20","port":5959,"polling_rate":5,"timeout":3,"groups":"Public,camaras","mock_scenario":"normal"}
```

El host debe ser una IPv4, IPv6 o nombre DNS válido, sin protocolo ni puerto. El puerto admite 1–65535; polling, 2–300 segundos; timeout, 1–15 segundos. Los grupos se separan por comas y conservan mayúsculas/minúsculas. Vacío utiliza los valores por defecto del SDK, habitualmente **Public**; no supone enumerar todos los grupos privados.

**Guardar** aplica los ajustes sin reiniciar servicios. El interruptor **Disable** guarda inmediatamente la desactivación, oculta sus campos y la pestaña NDI, cancela las peticiones del navegador e interrumpe el helper que estuviera consultando una configuración ya desactivada. **Probar conexión** usa los campos del formulario una sola vez, sin guardarlos ni activar el módulo. Las fuentes muestran equipo, stream y dirección NDI, con búsqueda rápida y avisos discretos para fallos de conexión o runtime.

### API y consumo

- `GET /api/ndi/status`: requiere una sesión del panel. Desactivado devuelve exactamente `{"enabled":false}`, sin sockets, SDK, procesos ni acceso a la caché NDI. Activado devuelve el estado cacheado; al caducar el polling rate, una petición actualiza la caché compartida bajo bloqueo para evitar consultas duplicadas.
- `POST /api/ndi/test`: requiere administrador, misma sesión/origen y token `X-CSRF-Token`. Recibe `{ip,port,timeout,groups}` y devuelve el diagnóstico puntual; no modifica la configuración ni la caché. Opcionalmente admite `mock_scenario` en simulación.

La respuesta activa contiene `enabled`, `online`, `server`, `latency_ms`, `last_updated` en UTC, `warning`, `sources_count` y `sources` con `full_name`, `host`, `stream` y `url`. También informa `polling_rate` y `mock`. Un TCP cerrado devuelve `online:false`; TCP accesible sin runtime conserva `online:true` y la latencia, con un aviso de falta de libndi.

También devuelve `receivers`, `receivers_count`, `receivers_supported` y `receivers_observed`, además de `sources_observed`, `entities`, `events` y `server_last_seen`. Cada entidad histórica indica `direction` (`sender`/`receiver`), `device_role` (`both` cuando se observan ambas funciones bajo el mismo hostname), `available`, `first_seen`, `last_seen`, `last_checked` y `last_changed`.

No hay un daemon de sondeo. PHP-FPM, que ya gestiona `systemd`, atiende ambas rutas a través de Caddy y ejecuta `bin/ndi-probe.py` solo para una consulta necesaria. El helper queda limitado por timeout, se destruye al terminar y elimina su configuración temporal. El navegador programa la siguiente petición después de la respuesta para evitar solapamientos, y cancela su temporizador al cambiar de vista, ocultar la pestaña del navegador, cerrar sesión o desactivar el módulo. **Actualizar** consulta el último estado respetando la caché y el límite de frecuencia. Durante la consulta aparece **Consultando…** y el botón queda desactivado. Si el panel agota el tiempo de espera, muestra un aviso y vuelve a habilitarlo.

### Disponibilidad y registro de actividad

La tabla identifica emisores y receptores anunciados, permite filtrar por función y conserva los equipos que dejan de aparecer. Muestra **Disponible** si se ha observado en la consulta, **No visible** si una consulta válida ya no lo incluye, y **Sin comprobar** si la colección no se pudo consultar (por caída del servidor, falta de runtime o limitación del listener). Un fallo del monitor no demuestra que el equipo se haya apagado; la última vez visto se conserva. Un error HTTP del navegador también deja la disponibilidad sin comprobar.

El **Registro de actividad**, debajo de la tabla, guarda detecciones, desapariciones, recuperaciones, cambios de dirección, disponibilidad del Discovery Server y avisos de consulta. Solo se añaden cambios; los aciertos de caché y las consultas estables no duplican eventos. `last_seen` se actualiza únicamente al observar de nuevo la entidad y todos los tiempos persistidos están en UTC.

**Limpiar registro**, disponible para administradores, borra únicamente los eventos del servidor/grupos seleccionados. Conserva los equipos, estados y últimos avistamientos, actualiza la caché para que no reaparezcan los eventos borrados y mantiene los historiales de otras combinaciones. Se aplica mediante `POST /api/ndi/log/clear` con sesión y CSRF; no ejecuta una consulta NDI.

Los administradores pueden **Eliminar** una entrada marcada como **No visible**. El panel conserva el registro de actividad y vuelve a mostrar el equipo si lo detecta de nuevo. La eliminación se limita al historial del servidor y los grupos que se están consultando.

El archivo privado `/var/lib/fluge-control/ndi-history.json` conserva hasta 500 eventos y 1.000 entidades por combinación de servidor, grupos y modo demo/real, con un máximo de cuatro combinaciones recientes. Cambiar los grupos o el servidor usa otro historial y no clasifica como ausentes los equipos de la consulta anterior. Volver a esa combinación recupera sus registros. Las pruebas de conexión no alteran este historial, y desactivar el módulo o reinstalar el código no lo borra.

El historial refleja las consultas realizadas mientras el monitor está abierto. Los intervalos con el módulo pausado, desactivado o el navegador cerrado no tienen seguimiento; una desaparición se fecha al observar su ausencia, no como hora exacta de desconexión física.

### SDK y alcance de los resultados

Se han revisado la documentación oficial y las cabeceras `Processing.NDI.Find.h` y `Processing.NDI.structs.h` del SDK v6 para Linux antes de implementar el helper. El puerto 5959 usa el protocolo NDI, no HTTP. Primero se mide una conexión TCP; después se utiliza `libndi.so` con `ctypes`, `NDIlib_find_create_v2`, `NDIlib_find_wait_for_sources` y `NDIlib_find_get_current_sources`.

El helper también consulta los emisores anunciados con `NDIlib_send_listener_get_senders` cuando está disponible la API estándar de NDI 6.3 o posterior. Combina ambos resultados, deduplica coincidencias por nombre y dirección y utiliza el UUID anunciado para distinguir emisores con el mismo nombre. El listener aplica los grupos configurados, con Public como valor por defecto. [NDI Sender Listener](https://docs.ndi.video/all/developing-with-ndi/sdk/ndi-sender-discovery-and-monitor/ndi-sender-listener)

Para receptores se ha revisado `Processing.NDI.RecvListener.h` y se utiliza la API estándar `NDIlib_recv_listener_create`, `NDIlib_recv_listener_is_connected`, `NDIlib_recv_listener_wait_for_receivers` y `NDIlib_recv_listener_get_receivers`. Necesita un runtime/Discovery Server compatibles con NDI 6.2 o posterior y que los receptores se anuncien. Las funciones se detectan opcionalmente; un runtime antiguo mantiene la consulta de emisores y comunica que la recepción no se ha podido comprobar. También se copian los datos prestados de los receptores antes de destruir el listener. No se utilizan APIs Advanced de eventos o control.

Si el servidor devuelve cero receptores y usas NDI Studio Monitor, abre **Settings → Allow Receiver Advertising** y comprueba que NDI Tools usa el mismo Discovery Server que el panel. En otras aplicaciones, el anuncio depende de que el fabricante lo haya implementado. [Guía oficial de NDI](https://docs.ndi.video/all/using-ndi/ndi-tools/ndi-tools-for-windows/discovery/getting-started-with-discovery-service)

El rol identifica una fuente emisora o un receptor anunciado. No certifica que se estén transmitiendo/recibiendo fotogramas en ese instante ni indica a qué fuente está conectado un receptor; esa telemetría no está incluida en esta consulta de la API estándar. Referencia: [descubrimiento y límites de monitorización de receptores NDI](https://docs.ndi.video/all/developing-with-ndi/sdk/ndi-recv-discovery-monitor-and-control).

Cada consulta dispone de su propio `ndi-config.v1.json` privado, con `ndi.networks.discovery` apuntando a `host:puerto`, y establece `NDI_CONFIG_DIR` antes de cargar e inicializar la biblioteca. `p_groups` y `ndi.groups.recv` reciben los grupos configurados. No se usa `p_extra_ips` para el Discovery Server: ese campo consulta equipos emisores mediante otro mecanismo. Las cadenas prestadas por el SDK se copian antes de destruir el finder; la dirección `p_url_address` se conserva sin reinterpretarla.

El finder estándar puede combinar fuentes del Discovery Server con las descubiertas mediante mDNS. Por eso la interfaz habla de **fuentes detectadas**: su lista depende del registro, los grupos, la conectividad y el tiempo de descubrimiento disponible, y no garantiza ser un volcado exclusivo/completo del registro del servidor. No se abre un receptor de vídeo o audio.

Referencias primarias: [NDI-FIND](https://docs.ndi.video/all/developing-with-ndi/sdk/ndi-find), [configuración del SDK y puerto del Discovery Server](https://docs.ndi.video/all/developing-with-ndi/sdk/configuration-files), [NDI_CONFIG_DIR y dependencias Linux](https://docs.ndi.video/all/developing-with-ndi/sdk/platform-considerations). La integración de red real requiere validación con tu servidor y runtime; las pruebas del repositorio usan simulación y sockets de loopback.

### Instalar libndi en Debian 12

La biblioteca debe estar en el **CT del panel/Caddy**, aunque Discovery Server se ejecute en otro CT. El despliegue instala automáticamente Python 3 y las bibliotecas Avahi necesarias junto con PHP-FPM. El runtime NDI sigue siendo opcional; su ausencia genera el fallback TCP.

Si aparece **Online** junto a **Falta libndi**, la latencia corresponde a la conexión TCP al servidor. En ese caso las fuentes y los receptores aparecen como pendientes de comprobar. Para detectar receptores anunciados, utiliza un runtime y un Discovery Server compatibles con NDI 6.2 o posterior.

Con una cuenta administradora, pulsa **Instalar runtime NDI** junto al aviso. El CT del panel descarga el SDK oficial y muestra su licencia. Cuando aceptas, el instalador prepara Avahi y publica `libndi` en `/opt/ndi-runtime/lib`. El panel vuelve a comprobar la conexión al terminar. En modo muestra, este flujo usa una licencia de ejemplo y datos simulados.

El mismo instalador se puede ejecutar como `root` en la consola del CT del panel Debian amd64:

```bash
/opt/fluge-control/deploy/install-ndi-runtime.py --interactive
```

Si el panel es de una revisión anterior, puedes instalar manualmente con el SDK oficial desde la consola del CT:

```bash
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl libavahi-client3 libavahi-common3 libstdc++6 libgcc-s1 avahi-daemon
mkdir -p /root/ndi-sdk-runtime
cd /root/ndi-sdk-runtime
curl -fL https://downloads.ndi.tv/SDK/NDI_SDK_Linux/Install_NDI_SDK_v6_Linux.tar.gz -o sdk.tar.gz
tar -xzf sdk.tar.gz
bash ./Install_NDI_SDK_v6_Linux.sh
install -d -m 0755 /opt/ndi-runtime/lib
cp -a './NDI SDK for Linux/lib/x86_64-linux-gnu/'libndi.so* /opt/ndi-runtime/lib/
printf '%s\n' /opt/ndi-runtime/lib > /etc/ld.so.conf.d/ndi-runtime.conf
ldconfig
systemctl enable --now avahi-daemon
```

Si el fabricante cambia el nombre del `.sh`, utiliza el incluido en el archivo descargado. La biblioteca se busca mediante el cargador del sistema y en `/opt/ndi-runtime/lib`. Para entornos de diagnóstico también se admite `NDI_LIBRARY_PATH` en el entorno del helper; PHP-FPM limpia el entorno y requeriría declararlo expresamente en su pool. Después de instalar la biblioteca, vuelve a **Probar conexión**; no hace falta reiniciar el panel. Los ajustes del monitor no modifican NICs, VLANs ni la configuración del servidor NDI.

### Demo y pruebas

El comando habitual `python scripts/preview-panel.py` mantiene todos los accesos de red simulados, incluido NDI. Activa el módulo, usa por ejemplo `192.168.10.20:5959` y guarda; aparecerá la pestaña **NDI Discovery**. Los escenarios del formulario son **Normal / Online**, **Offline / Timeout**, **0 fuentes** y **Aviso sin libndi**. Normal muestra CAM-01, CAM-02, VMIX-MAIN, PROPRESENTER y DECODER-HALL; cada ciclo actualiza hora y latencia entre 1 y 4 ms. También se puede forzar la simulación del monitor mediante `NDI_MOCK=true` en el entorno del backend. Los escenarios solo alteran el diagnóstico cuando está activa la simulación.

Normal también anuncia dos receptores, en VMIX-MAIN y DECODER-HALL, para probar equipos con ambas funciones. **0 fuentes** simula una consulta válida vacía y genera eventos de desaparición; volver a Normal registra las recuperaciones. **Offline** y **Aviso sin libndi** conservan los registros y los dejan sin comprobar, manteniendo el último avistamiento. El registro persiste al recargar la página.

La suite Python incluye validaciones, permisos, persistencia, caché y desactivación, pruebas TCP con loopback y una biblioteca falsa que comprueba el ABI y el ciclo de vida del finder. `tests/ndi.mjs` cubre validación del navegador; `tests/panel-browser.mjs` comprueba formulario, conexión, filtro, escenarios, polling, ausencia de consultas con el módulo desactivado o la vista oculta y tamaños móviles. Las pruebas utilizan estado temporal independiente y no necesitan un servidor ni `libndi` reales.

## Estado y sincronización

`services.json` proporciona la semilla de la primera instalación. Después, el inventario operativo está en `/var/lib/fluge-control/inventory.json`; `sync.sh` lo exporta desde el CT y conserva las ediciones de la web. Volver a instalar el código no reemplaza ese archivo, los usuarios ni las credenciales existentes. El plan DNS aplicado por el host transfiere la propiedad de sus reglas al panel para que puedan actualizarse o retirarse desde la web.

| Ruta en el CT | Contenido |
| --- | --- |
| `/opt/fluge-control/` | Código propiedad de root; `public/` es la única raíz web |
| `/var/lib/fluge-control/` | JSON privados, cuentas y seguimiento DNS; www-data, directorio 0700 y archivos 0600 |
| `/etc/caddy/Caddyfile` | Sitio del panel y conexión FastCGI con su pool PHP |
| `/etc/caddy/studio-routes.caddy` | Redirecciones 302 de los servicios |
| `/etc/php/8.2/fpm/pool.d/fluge-control.conf` | Pool nativo con socket local accesible por Caddy |
| `/usr/local/sbin/fluge-control-apply-routes` | Helper root sin argumentos, validación y rollback |
| `/etc/fluge-control/site.json` | Nombre del panel reservado por el despliegue |

El administrador del panel tiene una contraseña inicial aleatoria guardada en `.secrets/panel-admin.json` en el host; es independiente de AdGuard. La conexión inicial con AdGuard se toma de `.secrets/adguard-admin.json` cuando existe. Si cambias esa contraseña, actualízala en Configuración. Las sesiones y los permisos se verifican en el servidor; cada escritura requiere CSRF y una revisión vigente. El pool de producción fija `FLUGE_DEMO=0`, `NDI_MOCK=false` y `/usr/bin/python3`; el paquete instalado excluye el router de desarrollo y las cachés Python. El login abre con los campos vacíos.

El servidor web no escribe Caddyfile ni ejecuta recargas arbitrarias. El helper solo acepta el plan JSON fijado en el estado privado, vuelve a validarlo con código de confianza, ejecuta `caddy validate` y recarga Caddy. Si falla, restaura las rutas anteriores. Los bloqueos siguen el mismo orden en la API y el despliegue. La sincronización Caddy/AdGuard entre CTs no es una transacción conjunta.

Para recuperar el panel, conserva el estado privado junto con Caddy y las credenciales del host. Las copias del código y las rutas anteriores quedan en `/var/backups/studio-infra/caddy/change-XXXXXXXX/`; el inventario privado se conserva durante una reversión.

`./sync.sh --render-only` genera `generated/caddy/index.html` como referencia estática bajo demanda. La aplicación funcional está en `panel/public/`; colores y geometría se editan en `style.css`, y `logo.svg` se empaqueta desde el vector de la raíz. Consulta la [guía general](../../README.md) para DNS de los clientes e instalación de los cuatro CTs.

Referencias de integración: [PHP FastCGI en Caddy](https://caddyserver.com/docs/caddyfile/directives/php_fastcgi), [redirecciones Caddy](https://caddyserver.com/docs/caddyfile/directives/redir) y [API de AdGuard Home](https://github.com/AdguardTeam/AdGuardHome/blob/master/openapi/openapi.yaml).
