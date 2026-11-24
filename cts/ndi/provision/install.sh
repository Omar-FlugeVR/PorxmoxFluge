#!/usr/bin/env bash
# El host envía este archivo al CT y lo ejecuta manteniendo stdin interactivo.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
stage="$1"
timezone="$2"
source="$3"
url="$4"
expected_sha256="$5"
[[ "$stage" == /root/studio-ndi-install.* && -d "$stage" ]] || exit 1
trap 'rm -rf -- "$stage"' EXIT
[[ ! -e /opt/ndi-discovery ]] || { printf 'NDI ya existe; no se reinstala.\n' >&2; exit 1; }
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl python3 tzdata \
    binutils libstdc++6 libgcc-s1 libavahi-client3
ln -snf "/usr/share/zoneinfo/$timezone" /etc/localtime
printf '%s\n' "$timezone" > /etc/timezone
if [[ "$source" == download ]]; then
    curl --fail --silent --show-error --location --retry 3 --user-agent 'Mozilla/5.0' \
        "$url" --output "$stage/installer.download"
fi
if [[ -n "$expected_sha256" ]]; then
    printf '%s  %s\n' "$expected_sha256" "$stage/installer.download" | sha256sum --check --status
fi

# El tar oficial contiene un único autoextraíble .sh. No extraer rutas arbitrarias.
python3 - "$stage" <<'PY'
from pathlib import Path
import shutil
import sys
import tarfile
stage = Path(sys.argv[1])
source = stage / 'installer.download'
target = stage / 'vendor-install.sh'
with source.open('rb') as downloaded:
    is_script = downloaded.read(2) == b'#!'
if is_script:
    shutil.copyfile(source, target)
else:
    with tarfile.open(source, 'r:gz') as archive:
        candidates = [entry for entry in archive.getmembers() if entry.isfile() and entry.name.endswith('.sh')]
        if len(candidates) != 1:
            sys.exit('El archivo NDI debe contener un único instalador oficial .sh.')
        with archive.extractfile(candidates[0]) as src, target.open('wb') as out:
            shutil.copyfileobj(src, out)
PY
mkdir "$stage/payload"
cd "$stage/payload"
# El fabricante muestra el EULA y pide y/Y. No se envía una respuesta automática.
# cat muestra el acuerdo completo sin depender de un paginador o un TTY interno.
PAGER='cat' bash "$stage/vendor-install.sh"

mapfile -d '' binaries < <(find "$stage/payload" -type f -path '*/bin/x86_64-linux-gnu/ndi-discovery-server' -print0)
[[ "${#binaries[@]}" == 1 ]] || {
    printf 'El paquete no ha generado un único ndi-discovery-server Linux x86_64. Comprueba si se aceptó su licencia.\n' >&2
    exit 1
}
binary="${binaries[0]}"
readelf -h "$binary" | grep -q 'Advanced Micro Devices X86-64'
id ndi >/dev/null 2>&1 || useradd --system --home-dir /var/lib/ndi-discovery --create-home --shell /usr/sbin/nologin ndi
install -d -m 0755 /opt/ndi-discovery/bin /opt/ndi-discovery/lib /etc/ndi
install -m 0755 "$binary" /opt/ndi-discovery/bin/ndi-discovery-server
sdk_root="${binary%/bin/x86_64-linux-gnu/ndi-discovery-server}"
if [[ -d "$sdk_root/lib/x86_64-linux-gnu" ]]; then
    shopt -s nullglob
    libraries=("$sdk_root/lib/x86_64-linux-gnu/"libndi.so*)
    if [[ "${#libraries[@]}" -gt 0 ]]; then cp -a -- "${libraries[@]}" /opt/ndi-discovery/lib/; fi
fi
LD_LIBRARY_PATH=/opt/ndi-discovery/lib ldd /opt/ndi-discovery/bin/ndi-discovery-server > "$stage/ldd.txt"
if grep -q 'not found' "$stage/ldd.txt"; then cat "$stage/ldd.txt" >&2; exit 1; fi
# Conservar la cabecera del instalador, que incluye el acuerdo mostrado al usuario.
python3 - "$stage/vendor-install.sh" <<'PY'
from pathlib import Path
import sys
lines = []
with Path(sys.argv[1]).open('rb') as source:
    for line in source:
        if line.rstrip() == b'__NDI_ARCHIVE_BEGIN__':
            break
        lines.append(line)
        if sum(map(len, lines)) > 1024 * 1024:
            break
Path('/opt/ndi-discovery/installer-license-header.txt').write_bytes(b''.join(lines))
PY
