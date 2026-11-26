# Infraestructura local de estudio sobre Proxmox VE

Instalador Bash para desplegar **cuatro CTs Debian 12 en Proxmox: AdGuard Home, Caddy, Bitfocus Companion y NDI Discovery Service**. Cada CT ejecuta su software instalado directamente y gestionado con `systemd`, sin Docker. El CT de Caddy incluye el panel Fluge, con PHP-FPM nativo: servicios, libreta de equipos, escaneo, usuarios y publicación de accesos. `services.json` inicializa el inventario; después se administra desde la web y los scripts conservan ese estado.

| CT | Recursos | IP de ejemplo | Arranque |
| --- | --- | --- | --- |
| AdGuard Home, ID 110 | 1 vCPU, 512 MiB RAM, disco 4 GiB | `192.168.1.11` | `onboot=1`, orden 1, espera 15 s |
| Caddy y panel, ID 111 | 1 vCPU, 512 MiB RAM, disco 4 GiB | `192.168.1.12` | `onboot=1`, orden 2, espera 15 s |
| Bitfocus Companion, ID 112 | 2 vCPU, 2048 MiB RAM, disco 8 GiB | `192.168.1.15` | `onboot=1`, orden 3, espera 15 s |
| NDI Discovery Service, ID 113 | 1 vCPU, 512 MiB RAM, disco 4 GiB | `192.168.1.16` | `onboot=1`, orden 4, espera 15 s |

Los cuatro se crean como **unprivileged**, con `nesting=1` para que systemd pueda aislar los servicios de Debian 12. En Proxmox, **CT es el nombre de un contenedor LXC**: aquí equivale a un Debian independiente con su aplicación dentro. No se ejecutan contenedores adicionales dentro de los CTs. Proxmox VE es el host y no ocupa un quinto CT.

## 1. Preparar el host y el repositorio

Necesitas un Proxmox VE operativo sobre **amd64**, acceso `root`, salida a Internet, al menos un puente Linux y una IP libre fuera del rango DHCP por NIC. Los CTs siempre usan Debian 12, aunque el host use otra versión de Debian. El almacenamiento de plantillas debe admitir `vztmpl` y el de discos, `rootdir`.

En un Proxmox recién instalado, **instala Git antes de ejecutar `git clone`**. Pega el bloque completo en el Shell del nodo como `root`.

```bash
apt-get update
apt-get install -y git curl ca-certificates python3 &&
git clone https://github.com/Omar-FlugeVR/PorxmoxFluge.git fluge-proxmox &&
cd fluge-proxmox &&
./install.sh
```

`fluge-proxmox` es el nombre local de la carpeta en el host. Si ya clonaste el proyecto como `studio-infra`, puedes seguir usando esa carpeta para instalar y sincronizar.

Si el host recién instalado tiene repositorios Enterprise sin una suscripción, desactívalos desde la interfaz de Proxmox y añade el repositorio `no-subscription` de la versión del host para poder instalar dependencias. Si ya tienes `git`, `curl` y `python3`, el paso opcional del instalador puede encargarse de esos repositorios.

El cuestionario ofrece primero el post-install comunitario. Tras confirmar, aplica el siguiente perfil a partir de una [revisión fijada de community-scripts/ProxmoxVE](https://github.com/community-scripts/ProxmoxVE/blob/11acd8db754db96c9088762e2deff9c16f0a35f7/tools/pve/post-pve-install.sh).

| Opción | Respuesta |
| --- | --- |
| Repositorios Enterprise de PVE y Ceph | Desactivar |
| Repositorio `pve-no-subscription` | Activar o conservar activo |
| Añadir o reemplazar repositorios de paquetes Ceph | No |
| Añadir `pvetest` | No |
| Quitar el aviso de suscripción | Sí |
| Cambiar High Availability o Corosync | No |
| Actualizar Proxmox VE | Sí |
| Reiniciar el host ahora | No |

Antes de actualizar, se comentan las entradas Ceph Enterprise activas en los archivos `.list`, con copia previa en `*.fluge-before-post-install`. Las demás entradas de esos archivos se conservan. En Proxmox 9 el script comunitario también desactiva Ceph Enterprise en el formato `.sources`. La actualización puede reiniciar servicios y un kernel nuevo se usará al reiniciar el nodo manualmente.

Después responde sí o no para AdGuard, Caddy, Companion y NDI. El asistente detecta y muestra la IP de Proxmox que usará para el enlace a la consola web. Reutiliza la IP guardada si sigue asignada al host. Pulsa Intro para aceptar los valores entre corchetes y cambia los nombres de almacenamiento si el host usa otros.

| Pregunta | Valor de ejemplo y uso |
| --- | --- |
| Almacenamiento para plantillas de CT | `local` guarda la plantilla Debian 12 con la que se crean los CT. |
| Almacenamiento para discos de CT | `local-lvm` guarda los discos raíz de los CT. |
| DNS IPv4 para descargar paquetes | `1.1.1.1` resuelve nombres desde los CT durante la instalación. |
| DNS a los que AdGuard reenvía consultas | `1.1.1.1 9.9.9.9` son los resolutores que AdGuard consulta para los nombres externos. |
| Sufijo de los nombres DNS locales | `lan` se añade a los nombres de los servicios. |
| Nombre DNS del panel | `inicio` da lugar a `http://inicio.lan` cuando los clientes usan el DNS local. |

Para cada NIC indica el puente, la IPv4, la VLAN opcional y la puerta de enlace. Puedes escribir la IPv4 con su prefijo, como `192.168.1.20/24`, o escribir solo la dirección y responder después por el prefijo. Usa el de la red conectada a ese puente o VLAN. Si un valor de red es inválido, el asistente vuelve a pedir ese campo. Usa `-` para vaciar un valor opcional. La primera NIC lleva la IP del servicio y puedes configurar una puerta de enlace por CT. Prepara en Proxmox los puentes que vayas a usar y las VLAN permitidas.

Para una NIC nueva en el puente de la ruta por defecto del host y sin VLAN, el cuestionario puede sugerir la puerta de enlace de Proxmox si encaja con la subred. Cuando eliges una puerta de enlace, se ofrece la misma para los siguientes CTs que compartan puente, VLAN y subred. Pulsa Intro para usarla o escribe otra dirección.

El resumen muestra la red y las operaciones previstas. Al confirmar se guarda `config.env` con permisos `600` y se instalan los CTs elegidos. Si ya existía un archivo distinto, su copia queda en `config.env.bak`. Puedes conservar la configuración de un CT y excluirlo de la selección de esta ejecución.

## 2. Validar e instalar

```bash
./sync.sh --check
./sync.sh --render-only
```

Estos comandos necesitan Bash y Python 3 con la base de zonas horarias IANA (`tzdata` en Debian). `--check` valida los archivos locales. `--render-only` genera los artefactos para la selección guardada a partir de la semilla local. Los enlaces `.lan` funcionarán una vez configurado el DNS de los clientes.

El despliegue usa una plantilla Debian 12 amd64 del storage o la descarga con `pveam`. Crea los CTs elegidos con sus NIC y órdenes de arranque. Dentro de cada CT instala el software correspondiente.

- AdGuard usa su instalador oficial y la API local del asistente para generar `AdGuardHome.yaml`. Ofrece DNS en `53/UDP` y `53/TCP` y administración web en `80/TCP`.
- Caddy se instala desde su repositorio `apt` con PHP 8.2-FPM, cURL y el panel. Puedes abrirlo por su IP si aún no has desplegado el DNS.
- Companion usa el instalador headless oficial de Bitfocus con su runtime incluido y la unidad `companion.service`.
- NDI instala `ndi-discovery-server` como `ndi-discovery.service` a partir del SDK oficial. El cuestionario muestra la licencia al elegir un CT de NDI nuevo, antes de crear los CTs.

Cuando se comprueba que el CT de NDI Discovery está instalado, el panel activa por defecto el monitor con la IP y el puerto de ese CT. También se aplica al instalar NDI después del panel. Puedes decidir el destino y el estado del monitor desde Ajustes, y el despliegue conserva esa elección.

Las aplicaciones se comprueban antes de publicar el inventario en Caddy y AdGuard. Companion y NDI tienen verificaciones de respuesta HTTP y TCP, respectivamente. La descarga NDI puede necesitar que obtengas el paquete desde su web si el enlace directo no está disponible; en ese caso usa `NDI_INSTALLER_FILE` siguiendo la [guía del CT NDI](cts/ndi/README.md).

Las contraseñas iniciales de AdGuard y del administrador independiente del panel son aleatorias. Consúltalas como `root` en el host:

```bash
cat .secrets/adguard-admin.json
cat .secrets/panel-admin.json
```

El directorio tiene permisos `700` y el archivo, `600`; están excluidos de Git. La copia temporal dentro del CT se elimina al terminar el aprovisionamiento. Guarda esas credenciales. Si cambias la contraseña desde AdGuard, este archivo seguirá mostrando la **inicial**; `sync.sh` no restablece usuarios ni contraseñas.

Sin DNS local configurado todavía, abre `http://192.168.1.11` para administrar AdGuard. Para entrar al sistema de un CT usa `pct enter 110` o `pct enter 111`; no se habilita una contraseña root ni se necesita SSH dentro de los CTs.

Para reutilizar la selección guardada:

```bash
./install.sh --from-config  # Usa config.env y solicita confirmaciones
./install.sh --yes          # Crea los CTs seleccionados que falten
```

`--yes` omite el post-install y **respeta todos los CTs existentes**. Para un CT de NDI nuevo, reutiliza la aceptación guardada en `config.env` si coincide con la licencia del paquete actual. La primera aceptación y los cambios de licencia se confirman en una terminal antes de crear los CTs. Para autorizar únicamente la actualización de configuración en CTs existentes, usa `./install.sh --yes --update-existing`. `--post-install` permite ofrecer el post-install también junto a `--yes`, pero siempre exige confirmación en una terminal.

Para editar a mano desde cero, copia `config.env.example` a `config.env`, ajusta los datos y usa `--from-config`. El archivo permite fijar puertos, zona horaria, versión de Companion y origen del instalador NDI. Protege `config.env` y su copia porque el instalador los lee como configuración Bash de confianza.

## Gestionar cada CT por separado

Los comandos generales de la raíz instalan y sincronizan los cuatro CTs. Para trabajar sobre uno solo, usa sus puntos de entrada, siempre desde la terminal del **host Proxmox**:

| CT | Instalar | Sincronizar configuración | Documentación |
| --- | --- | --- | --- |
| AdGuard Home | `./cts/adguard/install.sh` | `./cts/adguard/sync.sh` | [Módulo AdGuard](cts/adguard/README.md) |
| Caddy | `./cts/caddy/install.sh` | `./cts/caddy/sync.sh` | [Módulo Caddy](cts/caddy/README.md) |
| Companion | `./cts/companion/install.sh` | `./cts/companion/sync.sh` | [Módulo Companion](cts/companion/README.md) |
| NDI Discovery | `./cts/ndi/install.sh` | `./cts/ndi/sync.sh` | [Módulo NDI](cts/ndi/README.md) |

También puedes seleccionar el CT desde la raíz:

```bash
./install.sh --ct adguard
./install.sh --ct caddy
./install.sh --ct companion
./install.sh --ct ndi
./sync.sh --ct adguard --yes
./sync.sh --ct caddy --render-only
```

Los comandos por CT conservan las opciones generales y solo crean o actualizan el CT seleccionado. Companion y NDI pueden sincronizarse sin que los demás CTs estén arrancados y sin leer `services.json`. Para sincronizar AdGuard o Caddy, el CT del panel debe estar accesible si ya existe: se comprueba su identidad y se exporta su inventario para conservar lo añadido desde la web. Si está detenido, arráncalo con `pct start <CADDY_CT_ID>`; un inventario o exportador incompleto detiene la operación, sin sustituirlo por la semilla. Al instalar desde cero por separado, sigue el orden de la tabla.

La configuración de red (`config.env`) y el inventario inicial (`services.json`) permanecen en la raíz. Una vez inicializado el panel, el inventario operativo reside en `/var/lib/fluge-control/inventory.json` dentro del CT de Caddy. `sync.sh` lo exporta mediante `pct` y conserva lo añadido desde la web; no reimporta la semilla ni sobrescribe ese inventario. Configura Companion y NDI, publica Caddy y después anuncia los nombres por DNS. Los scripts internos de cada módulo instalan el software directamente dentro del Debian del CT mediante `pct exec`.

`--check` y `--render-only` son siempre locales: validan o generan a partir de `config.env` y del inventario inicial, sin consultar `pct` aunque esté instalado. Para Companion y NDI basta la configuración, porque no usan el inventario. Estos modos no comprueban el estado actual de la web ni de Proxmox. En la sincronización real, una semilla eliminada o desactualizada no bloquea el uso de un inventario operativo válido.

## 3. Qué hace el DNS y qué hace Caddy

```text
Cliente ── DNS a AdGuard ── *.lan ─────── IP de Caddy
                      └── ndi.lan ───── IP del servidor NDI

http://inicio.lan/      ── Caddy + PHP-FPM ── panel Fluge
http://companion.lan/   ── Caddy ── 302 a http://192.168.1.15:8000/
ndi.lan                ── conexión del cliente NDI al servidor NDI
```

Los sitios de Caddy usan **HTTP explícito**, sin certificados ni HTTPS automático. Cada acceso de tipo `redirect` usa `redir "<target>{uri}" 302`: el navegador termina conectándose directamente al equipo y conserva ruta y query de la petición. No hay `reverse_proxy`. Por ejemplo, `http://proxmox.lan/?console=1` redirige a `https://192.168.1.10:8006/?console=1`.

Los certificados autofirmados del equipo destino siguen siendo responsabilidad del navegador. La redirección no los sustituye. Los servicios `dns_only` no tienen un sitio de Caddy ni un enlace web inventado; su tarjeta muestra el nombre DNS y la IP para usarlos en el cliente correspondiente.

El panel usa HTML, CSS y JavaScript nativos, sin fuentes remotas, frameworks ni build. Conserva el fondo oscuro, las formas geométricas y el logo vectorial de Fluge. Busca por nombre/IP/dominio, filtra por categorías y ofrece vistas de Servicios, Equipos, Escaneo y Configuración. `/` enfoca el buscador y `Escape` lo limpia. La API PHP requiere sesión; las escrituras comprueban permisos, CSRF y revisiones para impedir sobrescrituras desde otra sesión. Las cuentas locales usan hashes Argon2id.

## 4. Añadir equipos y sincronizar

Desde el panel, **Añadir servicio → Guardar** guarda la ficha y publica automáticamente sus accesos en Caddy y AdGuard dentro de la misma petición al servidor. Editar, eliminar e importar inventario y guardar ajustes siguen el mismo flujo. Si una integración falla, los datos siguen guardados y la respuesta indica publicación parcial; la interfaz muestra el error y una opción para reintentar. La aplicación entre Caddy y AdGuard no es una transacción conjunta. Las reglas DNS ajenas al panel se conservan.

Configura la conexión con AdGuard en Configuración. Al instalar ambos CTs, se utiliza la credencial inicial guardada por el instalador. Si AdGuard ya existía o cambias su contraseña, actualiza esa conexión en el panel. El acceso al panel tiene su propia cuenta independiente.

**Configuración → Categorías** permite crear, renombrar y eliminar las categorías compartidas. Los cambios se reflejan en los filtros, servicios, equipos y formularios. Renombrar conserva las asociaciones; al eliminar una categoría con elementos hay que elegir otra a la que trasladarlos. Se mantiene al menos una categoría. Cada cambio se guarda y aplica automáticamente en una sola revisión, sin borrar servicios ni equipos.

El escáner comprueba puertos TCP, ARP local y nombres de `/etc/hosts`/NetBIOS. Incluye los puertos 8000 de Companion y 5959 de NDI. Se limita a las redes privadas permitidas; las MAC por ARP solo se obtienen en el segmento accesible al CT. El reconocimiento como registrado compara la IP con el inventario, no identifica automáticamente el modelo de cada equipo AV.

**Configuración → Redes y escaneo** permite añadir, editar y eliminar redes con nombre, CIDR y pool DHCP opcional. Cada red aparece como preset en Escaneo. El filtro DHCP utiliza el pool correspondiente a cada IP, también en búsquedas manuales que abarquen varias redes; si hay subredes solapadas, prevalece la más específica. La configuración anterior se conserva como redes iniciales. Estos ajustes se guardan y aplican automáticamente; son informativos y no cambian las NIC ni el DHCP del router.

**Configuración → Cuentas** reúne la gestión de usuarios y resalta la sesión actual. La cuenta principal no se puede eliminar ni desactivar; es la única que puede cambiar contraseñas, incluida la suya, desde el botón de cada fila y sin pedir la anterior. Crear usuarios y cambiar contraseñas exige repetir la nueva contraseña. Las demás cuentas, incluso administradoras, no pueden cambiar contraseñas. **Copia del inventario** contiene únicamente la importación y exportación de servicios, equipos y categorías.

**Configuración → NDI Discovery Server** activa el monitor opcional de conectividad TCP y fuentes NDI. Permite configurar host, puerto, polling, timeout y grupos, probar la conexión y guardar sin reinicios. La pestaña NDI Discovery muestra estado, latencia y fuentes con buscador; solo consulta mientras está visible. Desactivar guarda inmediatamente y detiene las consultas. Se reutiliza PHP-FPM con un helper Python nativo, sin otro servidor ni contenedores adicionales.

La misma muestra local incluye cinco fuentes AV simuladas y los escenarios Online, Offline, 0 fuentes y falta de libndi. Arranca con `python scripts/preview-panel.py`, activa NDI en Configuración, introduce una IP de ejemplo y guarda. En la instalación real, sin `libndi`, funciona la comprobación TCP y aparece un aviso; consulta la [instalación del runtime y los detalles del SDK](cts/caddy/README.md#monitorización-ndi-opcional).

NDI incluye un **Registro de actividad** persistente al pie de la vista y conserva los equipos con su última observación y disponibilidad. Distingue emisores y receptores anunciados (recepción requiere NDI 6.2+), con filtros por función; la demo añade dos receptores para mostrar equipos con ambas funciones. Las caídas del monitor dejan los equipos «Sin comprobar» y conservan su última vez visto. El historial se actualiza mientras se consulta NDI; no registra los intervalos de sondeo pausado ni certifica tráfico de vídeo/audio.

**Antes de la primera instalación**, puedes preparar los servicios iniciales en `services.json`:

```json
{
  "name": "Mezclador de vídeo",
  "subdomain": "mezclador",
  "target": "http://192.168.1.30:8080",
  "category": "Control / Broadcast",
  "description": "Interfaz de control del mezclador principal.",
  "mode": "redirect"
}
```

Para un servicio sin interfaz HTTP:

```json
{
  "name": "Servidor de vídeo",
  "subdomain": "video",
  "target": "",
  "ip": "192.168.1.40",
  "category": "Red / Vídeo",
  "description": "Conectar desde la aplicación del fabricante.",
  "mode": "dns_only"
}
```

Todos los objetos incluyen `name`, `subdomain`, `target`, `category`, `description` y `mode`. Para `dns_only`, `target` es una cadena vacía e `ip` es obligatoria. Los subdominios deben ser únicos, en minúsculas y sin el dominio base; `inicio` queda reservado para la landing.

`target` admite una URL HTTP/HTTPS absoluta, puerto y prefijo de ruta. Usa una URL base **sin query, fragmento ni credenciales**, y evita apuntar de vuelta a Caddy o crear ciclos entre atajos. El generador elimina la barra final de la base antes de añadir `{uri}`. Escribe espacios de rutas codificados, por ejemplo `%20`.

En `target` e `ip` se admiten las variables `${PROXMOX_IP}`, `${ADGUARD_IP}`, `${CADDY_IP}`, `${COMPANION_IP}`, `${NDI_IP}`, `${COMPANION_PORT}` y `${NDI_PORT}`, definidas en `config.env`. No se ejecutan expresiones del inventario. Puedes usar IPs literales para equipos nuevos. Los ejemplos usan variables para que cambiar la red o el puerto de Companion no obligue a mantenerlos en dos archivos.

```bash
./sync.sh --check
./sync.sh             # solicita confirmación
# Para automatizaciones autorizadas:
./sync.sh --yes
```

La sincronización usa la selección guardada en `INSTALL_CTS` o todos los CTs con IP configurada en un archivo manual. Puedes seleccionar uno con `--ct adguard`, `--ct caddy`, `--ct companion` o `--ct ndi`. Los CTs deben estar en marcha en el nodo actual, con Debian 12, sin privilegios y con las NIC esperadas. Según la selección, actualiza:

- `/opt/fluge-control/`, `/etc/caddy/Caddyfile` y `/etc/caddy/studio-routes.caddy` en Caddy; valida con `caddy validate`, configura el pool PHP-FPM y recarga los servicios necesarios. El estado privado se conserva.
- `filtering.rewrites` en `/opt/AdGuardHome/AdGuardHome.yaml`. Detiene AdGuard antes de leer/escribir y lo arranca después: puede haber una breve interrupción DNS.
- El drop-in systemd de Companion, con su escucha IPv4 y puerto HTTP. Conserva botones, conexiones, perfil y versión del programa.
- La unidad y el JSON de NDI Discovery, con su IP y puerto TCP. Conserva el binario instalado y comprueba que el puerto responda.

Se conservan usuarios, filtros, upstreams y las demás opciones de AdGuard. Las reescrituras manuales de otros nombres también se conservan. La primera sincronización reemplaza cualquier regla que coincida con un nombre gestionado, incluido `*.lan`. El archivo `/var/lib/studio-infra/managed-rewrites.json` registra esos nombres para retirar excepciones antiguas al borrar servicios, pasar de `dns_only` a `redirect` o cambiar el dominio base. Conserva ese archivo al restaurar el CT.

Las reglas generadas llevan `enabled: true`, necesario en las versiones actuales de AdGuard. Si has desactivado globalmente las reescrituras desde su interfaz, la sincronización te pide reactivarlas antes de continuar.

Sin cambios de contenido, no se recargan servicios activos ni se generan nuevas copias. Editar `config.env` no migra CTs existentes: los cambios de IP, CIDR, bridge o ID requieren ajustar primero la infraestructura a mano. El panel también rechaza un cambio de su dominio o IP hasta migrar su configuración privada. Cambiar `UPSTREAM_DNS`, zona horaria o usuario inicial tampoco modifica instalaciones ya hechas; administra esas opciones dentro de sus servicios. El CT de Caddy nuevo utiliza 512 MiB; los CTs existentes no se redimensionan automáticamente.

### Actualizaciones del panel y de los CTs

La pestaña **Actualizaciones** está disponible para administradores. Muestra la versión instalada y la última publicación. `studio-infra-updates.timer` comprueba las versiones cada hora en Proxmox. **Recargar informe** lee el último resultado. `bash updates.sh` fuerza una consulta desde el host.

**Actualizar** pide confirmación para la versión mostrada. Proxmox recoge una solicitud a la vez mediante `studio-infra-update-jobs.timer`. La barra de progreso aparece al confirmar y sigue visible durante la cola y la ejecución. El panel vuelve a consultar al recuperarse de una recarga. Instala esta función sincronizando el CT de Caddy una primera vez.

La versión y el tipo de **Servicio de control** se leen de [`panel-version.json`](panel-version.json) en la raíz. Para publicar una actualización del panel, pon la versión que quieras mostrar y `"update_required": true` en el commit. Aparecerá **Actualización requerida** si ese commit está pendiente de desplegar. Con `"update_required": false`, el chequeo omite ese commit como actualización del panel, por ejemplo cuando cambias el instalador. La versión instalada se registra al sincronizar Caddy.

Para **Servicio de control**, GitHub publica el commit y Proxmox lo descarga desde la rama por defecto de `origin`. El host comprueba que es el commit elegido, avanza su copia local con `git merge --ff-only` y ejecuta `sync.sh` para Caddy. El despliegue copia el panel, valida Caddy y recarga Caddy y PHP-FPM. El inventario, los ajustes y las cuentas permanecen en el estado privado del CT. Si falla la validación o la recarga durante la publicación, el despliegue restaura los archivos anteriores. Los cambios locales en el checkout requieren revisión antes de actualizar desde el botón.

El despliegue desde la terminal sigue disponible. Ejecuta como root desde el checkout de Proxmox:

```bash
git pull --ff-only
./sync.sh --ct caddy --check
./sync.sh --ct caddy
```

| Servicio | Qué muestra y cómo se actualiza |
|---|---|
| Bitfocus Companion | Estable para Linux x86_64. Hace copia del programa y el perfil y usa el actualizador oficial de Bitfocus para la versión elegida. |
| AdGuard Home | Estable publicada en GitHub. Hace copia del programa y sus datos y ejecuta `AdGuardHome --update`. El DNS se interrumpe mientras se aplica. |
| Caddy | Estable publicada en GitHub. Usa la versión candidata de APT y descarga antes la versión anterior para poder recuperarla. Si APT todavía no ofrece la versión elegida, el servicio sigue funcionando con la instalada. |
| NDI Discovery Server | Compara la versión del SDK instalado con el paquete Linux oficial. Guarda el paquete en caché y reutiliza la descarga con los validadores HTTP disponibles. Pide aceptar el texto de la licencia si ha cambiado. Conserva los ajustes de Discovery Server. |

Los actualizadores de los CTs comprueban la versión, el servicio y su puerto después de iniciarlo. Las copias se guardan en `/var/backups/studio-infra/` dentro de cada CT. En el host, `journalctl -u studio-infra-update-jobs.service` muestra el resultado de las instalaciones y `journalctl -u studio-infra-updates.service` el de las consultas.

En el **CT de Caddy**, `/opt/fluge-control/revision.json` guarda la versión, el tipo y el commit del panel desplegado. `/etc/fluge-control/updates.json` conserva el último informe de versiones y su fecha. Proxmox lee la versión instalada de cada uno de los demás servicios en su propio CT. En el **host Proxmox**, `/var/cache/studio-infra/ndi/` conserva el paquete oficial de NDI y los metadatos de la versión comprobada. El primer chequeo de una instalación limpia genera estos datos de nuevo.

## 5. Configurar los clientes

En el DHCP del router, anuncia **la IP de AdGuard** como servidor DNS (`192.168.1.11` en el ejemplo), renueva las concesiones o reconecta los dispositivos. Otra opción es fijar ese DNS en la interfaz de red de cada PC.

No anuncies un resolutor público como DNS secundario si necesitas que todos los accesos `.lan` funcionen: los clientes pueden consultarlo también y no conoce tus nombres locales. Si el navegador usa DNS seguro/DoH con un proveedor externo, permite que los nombres locales se resuelvan con el DNS del sistema. Revisa también los DNS anunciados por IPv6 si tus clientes los usan.

Abre `http://inicio.lan/`. En Chrome/Edge, escribe **`companion.lan/` con la barra final la primera vez**, o la URL completa `http://companion.lan/`, para que la barra de direcciones lo interprete como una dirección y no como una búsqueda. Estos atajos sirven por HTTP; escribir `https://companion.lan` no contacta con un sitio TLS en Caddy.

Permite desde la LAN `53/UDP` y `53/TCP` hacia AdGuard, `80/TCP` hacia AdGuard y Caddy, `COMPANION_PORT/TCP` hacia Companion y `NDI_PORT/TCP` hacia NDI. Añade los puertos propios de los módulos AV y de Satellite que utilices. El firewall de Proxmox o del router puede requerir reglas existentes; el instalador no los modifica. Los servicios se destinan a la LAN: mantén sus puertos sin reenvíos desde Internet.

## Reejecuciones, copias y recuperación

Nunca se invoca `pct destroy`. Si un CT existe, el instalador avisa y permite omitirlo o actualizar su configuración, comprobando su identidad de red antes de arrancarlo y sus servicios antes de aplicar cambios. No vuelve a ejecutar los instaladores de AdGuard, Companion ni NDI sobre CTs preexistentes. La publicación del panel en Caddy añade sus dependencias PHP-FPM, cURL, Python, Avahi y sudo si faltan; fija `FLUGE_DEMO=0` y `NDI_MOCK=false`, y excluye el router de desarrollo del paquete instalado. Los nombres de host, recursos, arranque, red y `nesting` solo se establecen al crear CTs.

Antes de sustituir archivos, se valida la configuración y se conserva la anterior dentro de cada CT:

```text
/var/backups/studio-infra/caddy/change-XXXXXXXX/
/var/backups/studio-infra/adguard/change-XXXXXXXX/
/var/backups/studio-infra/companion/change-XXXXXXXX/
/var/backups/studio-infra/ndi-discovery/change-XXXXXXXX/
```

Un fallo de validación, recarga, arranque o comprobación de respuesta restaura los archivos del servicio afectado e intenta recuperar su estado anterior. **La operación conjunta no es una transacción entre CTs**: los servicios ya aplicados pueden conservar la configuración nueva si falla un paso posterior. Corrige la causa y vuelve a ejecutar `./sync.sh`. Las copias se conservan hasta que las retires manualmente y no sustituyen un backup `vzdump` ni la exportación del perfil de Companion.

Si falla el aprovisionamiento inicial, el CT creado se conserva para diagnóstico. Revisa el error y sus logs antes de intervenir: una reejecución no lo reinstala automáticamente. Para reutilizar un CT compatible administrado previamente, AdGuard debe estar inicializado en la ruta oficial, con `python3-yaml`, o Caddy debe disponer de su unidad `systemd` y su grupo `caddy`.

Diagnóstico desde el host, ajustando los IDs a tu configuración:

```bash
pct status 110
pct exec 110 -- systemctl status AdGuardHome --no-pager
pct exec 110 -- journalctl -u AdGuardHome -n 50 --no-pager
pct exec 111 -- systemctl status caddy --no-pager
pct exec 111 -- journalctl -u caddy -n 50 --no-pager
pct exec 111 -- caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
pct exec 112 -- journalctl -u companion -n 50 --no-pager
pct exec 113 -- journalctl -u ndi-discovery -n 50 --no-pager
# Desde un cliente con nslookup:
nslookup companion.lan 192.168.1.11
nslookup ndi.lan 192.168.1.11
# Desde el host, incluso antes de cambiar su DNS:
curl -I --resolve inicio.lan:80:192.168.1.12 http://inicio.lan/
curl -I --resolve companion.lan:80:192.168.1.12 http://companion.lan/
```

El primer nombre debe resolver a Caddy; el segundo, a la IP de NDI. Los comandos `curl` deben devolver `200` y `302`, respectivamente. Si el DNS sigue mostrando datos antiguos, espera la expiración de la caché o vacíala en el cliente. Publicar el nombre NDI no configura los clientes NDI, las VLANs ni el transporte del vídeo.

## Estructura y comprobaciones

```text
install.sh                       Instalador interactivo desde Proxmox
sync.sh                          Validación, previsualización y sincronización
config.env.example               Red y ajustes del despliegue
services.json                    Inventario inicial, antes de inicializar el panel
cts/adguard/
  install.sh, sync.sh             Comandos para gestionar solo AdGuard
  host.sh                        Recursos, validaciones y transferencias de AdGuard
  generate-rewrites.py            Generación del plan DNS
  provision/install.sh           Instalación nativa dentro de su CT
  provision/apply.py              Aplicación de rewrites y recuperación
  README.md                      Guía del CT
cts/caddy/
  install.sh, sync.sh             Comandos para gestionar solo Caddy
  host.sh                        Recursos, validaciones y transferencias de Caddy
  generate-caddy.py               Generación del Caddyfile y las rutas iniciales
  generate-seed.py                Conversión al inventario del panel
  generate-landing.py             Generación del HTML estático
  templates/landing.html          HTML, CSS y JS de la landing
  provision/install.sh           Instalación nativa dentro de su CT
  provision/apply.sh              Publicación, recarga y recuperación
  provision/panel.sh              PHP-FPM nativo y permisos del panel
  panel/public/                  Interfaz Fluge y API PHP
  panel/src/                     Inventario, sesión, escáner y sincronización
  panel/bin/                     Inicialización, exportación y compilación CLI
  panel/deploy/                  Helper restringido para publicar rutas Caddy
  README.md                      Guía del CT
cts/companion/
  install.sh, sync.sh             Comandos para gestionar solo Companion
  host.sh                        Recursos y operaciones desde Proxmox
  generate-config.py             Drop-in systemd y plan de comprobación HTTP
  templates/override.conf         Escucha IPv4 y puerto web
  provision/install.sh           Instalación nativa oficial de Bitfocus
  README.md                      Guía del CT
cts/ndi/
  install.sh, sync.sh             Comandos para gestionar solo NDI
  host.sh                        Recursos, licencia y operaciones desde Proxmox
  generate-config.py             Unidad, JSON y comprobación TCP
  templates/ndi-discovery.service Servicio nativo systemd
  provision/install.sh           Instalación del paquete oficial revisado en el host
  README.md                      Guía del CT
scripts/common.sh                Utilidades Proxmox compartidas
scripts/questionnaire.py         Encuesta y generación de config.env
scripts/ndi_installer.py         Preparación del paquete y aceptación de licencia NDI
scripts/ct-network.py            Opciones de red para pct y comprobación de NIC
scripts/provision/apply-service.py Copias, aplicación y rollback de servicios systemd
scripts/inventory.py             Validación y resolución de variables
scripts/render.py                Coordinación de los generadores por CT
generated/adguard/               Plan DNS generado (excluido de Git)
generated/caddy/                 Caddyfile y landing generados (excluidos de Git)
generated/companion/             Drop-in y plan de aplicación (excluidos de Git)
generated/ndi/                   Unidad, JSON y plan de aplicación (excluidos de Git)
assets/branding/logo-original.png Original raster del logo aportado
tests/                           Validación, idempotencia y recuperación
scripts/preview-panel.py          Servidor local con integraciones simuladas
```

## Desarrollo y pruebas

Para desarrollar en Debian/Ubuntu sin Proxmox:

```bash
apt-get install -y python3 python3-yaml shellcheck php-cli php-curl
python3 -m unittest discover -s tests -v
bash tests/test-bash.sh
bash tests/test-post-install.sh
shellcheck -x install.sh sync.sh updates.sh scripts/*.sh scripts/provision/*.sh cts/*/*.sh cts/*/provision/*.sh tests/*.sh
```

Las pruebas locales y la CI comprueban validación, generación por CT, selección de plantillas, rechazo de IDs ocupados y restauración ante fallos simulados. También ejecutan el coordinador Bash con dobles de Proxmox para comprobar instalación nueva, actualización, omisión de existentes, identidad antes del arranque y protección del inventario operativo ante fallos del panel. Las pruebas HTTP del panel usan un servidor PHP temporal con estado aislado; comprueban altas/bajas, publicación DNS/302, permisos, CSRF, revisiones y conservación de reglas ajenas. También comprueban el modo de producción con una contraseña privada aleatoria, sin simulación ni acceso mediante las credenciales de muestra. El helper de Caddy se prueba en POSIX. `tests/panel-browser.mjs` permite comprobar los flujos del panel, incluidos NDI y Actualizaciones, con Chromium mediante CDP; no requiere paquetes npm. La instalación real requiere comprobarla sobre un host Proxmox; estas pruebas no emulan LXC ni `systemd`.

### Muestra local de desarrollo

Necesitas PHP 8.2+ y Python 3 con zonas horarias IANA (en Windows: `pip install tzdata`). Desde el repositorio:

```bash
python scripts/preview-panel.py
# Si PHP no está en PATH:
python scripts/preview-panel.py --php /ruta/al/ejecutable/php --port 8787
```

Abre **http://127.0.0.1:8787/**. Acceso inicial: **admin / FlugeDemo2026**, introducido manualmente en el formulario. El servidor escucha exclusivamente en loopback. El inventario de muestra persiste en `generated/demo/`; no utiliza las credenciales de producción. El escaneo devuelve equipos de ejemplo, AdGuard guarda reglas simuladas y Caddy genera sus redirecciones sin ejecutar recargas. «Ver reglas aplicadas» permite comprobar el resultado de guardar, editar y eliminar servicios. Los enlaces a los equipos también se simulan. Detén el servidor con Ctrl+C.

Flujo para probar: **Escaneo → Iniciar escaneo → Registrar equipo → Añadir servicio → Guardar**. Guardar siempre publica automáticamente los accesos; no hay un paso de aplicación manual. Los equipos dentro del rango DHCP se señalan para asignarles una IP fija antes de registrarlos. El registro de un equipo guarda su ficha; crear un servicio con nombre de red es lo que publica DNS y, para accesos web, la redirección.

## Copias y recuperación

Además de `vzdump`, conserva `/var/lib/fluge-control/`, `/etc/fluge-control/site.json`, `/etc/caddy/` y las credenciales privadas del host. La exportación JSON de la web incluye solo el inventario. El usuario web no puede escribir los archivos de Caddy: un helper propiedad de root valida un plan estructurado, comprueba la configuración y restaura las rutas si falla la recarga. El helper no admite argumentos ni texto Caddyfile del navegador. Evita editar simultáneamente desde el panel y ejecutar una sincronización completa del host; la publicación entre CTs sigue sin ser una transacción conjunta.

Referencias utilizadas: [gestión de CTs Proxmox](https://pve.proxmox.com/pve-docs/chapter-pct.html), [instalador oficial de AdGuard Home](https://github.com/AdguardTeam/AdGuardHome/blob/master/scripts/install.sh), [configuración de AdGuard](https://adguard-dns.io/kb/adguard-home/configuration/), [API oficial del asistente](https://github.com/AdguardTeam/AdGuardHome/blob/master/openapi/openapi.yaml), [repositorio apt oficial de Caddy](https://caddyserver.com/docs/install#debian-ubuntu-raspbian), [directiva `redir`](https://caddyserver.com/docs/caddyfile/directives/redir), [instalador oficial de Companion](https://github.com/bitfocus/companion-pi) y [NDI Discovery en Linux](https://docs.ndi.video/all/using-ndi/ndi-tools/ndi-tools-for-windows/discovery/discovery-server-additional-information).
