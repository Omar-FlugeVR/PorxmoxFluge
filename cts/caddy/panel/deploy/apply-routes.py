#!/usr/bin/python3
"""Root helper: publish only a validated Fluge redirect plan, never raw Caddyfile."""

import fcntl
import json
import os
import pathlib
import pwd
import stat
import subprocess
import sys
import tempfile

STATE_DIR = pathlib.Path('/var/lib/fluge-control')
ROUTES_FILE = pathlib.Path('/etc/caddy/studio-routes.caddy')
RENDERER = pathlib.Path('/opt/fluge-control/bin/render-routes.php')
MAX_SIZE = 1024 * 1024


def read_candidate(path, expected_uid):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != expected_uid:
            raise ValueError('El plan debe ser un archivo regular propiedad del servicio web')
        if info.st_size > MAX_SIZE:
            raise ValueError('El plan supera el tamaño permitido')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            payload = stream.read(MAX_SIZE + 1)
        if len(payload) > MAX_SIZE:
            raise ValueError('El plan supera el tamaño permitido')
        return payload
    finally:
        os.close(fd)


def atomic_write(path, content):
    fd, name = tempfile.mkstemp(prefix='.fluge-routes-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def publish_routes(content, path=ROUTES_FILE, run=subprocess.run):
    existed = path.exists()
    previous = path.read_bytes() if existed else b''
    if existed and previous == content:
        return False
    atomic_write(path, content)
    reload_attempted = False
    try:
        checked = run(['/usr/bin/caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'], capture_output=True, timeout=20)
        if checked.returncode != 0:
            raise RuntimeError('Caddy rechazó el plan; se ha restaurado la configuración anterior')
        reload_attempted = True
        reloaded = run(['/usr/bin/systemctl', 'reload', 'caddy'], capture_output=True, timeout=20)
        if reloaded.returncode != 0:
            raise RuntimeError('No se pudo recargar Caddy; se ha restaurado la configuración anterior')
    except Exception as original_error:
        if existed:
            atomic_write(path, previous)
        else:
            path.unlink(missing_ok=True)
        if reload_attempted:
            try:
                restored = run(['/usr/bin/caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'], capture_output=True, timeout=20)
                if restored.returncode != 0:
                    raise RuntimeError('La configuración anterior no pasó la validación')
                restored = run(['/usr/bin/systemctl', 'reload', 'caddy'], capture_output=True, timeout=20)
                if restored.returncode != 0:
                    raise RuntimeError('La recarga de recuperación falló')
            except Exception as recovery_error:
                raise RuntimeError('Se restauró el archivo anterior, pero no se pudo confirmar la recarga de recuperación') from recovery_error
        raise
    return True


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise ValueError('Este comando requiere root y no admite argumentos')
    lock_dir = pathlib.Path('/run/fluge-control')
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    with (lock_dir / 'routes.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        payload = read_candidate(STATE_DIR / 'caddy-candidate.json', pwd.getpwnam('www-data').pw_uid)
        compiled = subprocess.run(
            ['/usr/bin/php', str(RENDERER)], input=payload, capture_output=True, timeout=20,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
        )
        if compiled.returncode or not compiled.stdout or len(compiled.stdout) > MAX_SIZE:
            raise ValueError('El generador rechazó el plan de rutas')
        changed = publish_routes(compiled.stdout)
        print(json.dumps({'status': 'ok', 'changed': changed}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'error', 'message': str(error)}), file=sys.stderr)
        sys.exit(1)
