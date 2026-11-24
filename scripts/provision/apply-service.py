#!/usr/bin/env python3
"""Aplica archivos systemd de Companion/NDI con backup, prueba de servicio y rollback."""

import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time


BACKUP_ROOT = Path("/var/backups/studio-infra")


def run(*args):
    subprocess.run(args, check=True)


def active(service):
    return subprocess.run(["systemctl", "is-active", "--quiet", service], check=False).returncode == 0


def enabled(service):
    return subprocess.run(["systemctl", "is-enabled", "--quiet", service], check=False).returncode == 0


def check_health(health):
    if health["kind"] == "http":
        connection = http.client.HTTPConnection(health["host"], health["port"], timeout=2)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            if not 200 <= response.status < 500:
                raise RuntimeError(f"HTTP {response.status}")
        finally:
            connection.close()
    elif health["kind"] == "tcp":
        with socket.create_connection((health["host"], health["port"]), timeout=2):
            pass
    else:
        raise ValueError("Prueba de servicio desconocida")


def wait_healthy(service, health):
    last_error = None
    for _ in range(60):
        try:
            if not active(service):
                raise RuntimeError(f"{service} no está activo")
            check_health(health)
            # Capturar también fallos inmediatamente después de abrir el puerto.
            time.sleep(1)
            if not active(service):
                raise RuntimeError(f"{service} se ha detenido")
            check_health(health)
            return
        except (OSError, http.client.HTTPException, RuntimeError) as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"El servicio {service} no responde: {last_error}")


def atomic_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd, temporary = tempfile.mkstemp(prefix=".studio-", dir=target.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, temporary)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply(plan, stage):
    service = plan["service"]
    if not re.fullmatch(r"[a-z][a-z0-9-]*", service):
        raise ValueError("Nombre de unidad inválido")
    entries = plan["files"]
    if not entries:
        raise ValueError("Plan vacío")
    paths = []
    for entry in entries:
        source, target = stage / entry["source"], Path(entry["target"])
        if Path(entry["source"]).name != entry["source"] or not target.is_absolute() or ".." in target.parts:
            raise ValueError("Ruta inválida en el plan")
        if target.is_symlink():
            raise ValueError(f"No se reemplaza un enlace simbólico: {target}")
        paths.append((source, target))
    unchanged = all(target.is_file() and source.read_bytes() == target.read_bytes() for source, target in paths)
    if unchanged:
        run("systemctl", "enable", service)
        if not active(service):
            run("systemctl", "start", service)
        wait_healthy(service, plan["health"])
        print(f"[OK] {service} sin cambios.")
        return
    root = BACKUP_ROOT / service
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix="change-", dir=root))
    records = []
    for index, (_source, target) in enumerate(paths):
        saved = backup / str(index)
        existed = target.exists()
        if existed:
            shutil.copy2(target, saved)
        records.append((target, saved, existed))
    (backup / "manifest.json").write_text(json.dumps([
        {"target": str(target), "backup": saved.name, "existed": existed}
        for target, saved, existed in records], indent=2) + "\n", encoding="utf-8")
    was_active = active(service)
    was_enabled = enabled(service)
    try:
        for source, target in paths:
            atomic_copy(source, target)
        run("systemd-analyze", "verify", plan["verify_unit"])
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", service)
        run("systemctl", "restart" if was_active else "start", service)
        wait_healthy(service, plan["health"])
        print(f"[OK] Copia anterior de {service}: {backup}")
    except BaseException:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print(f"[ERROR] Restaurando {service} desde {backup}", file=sys.stderr)
        subprocess.run(["systemctl", "stop", service], check=False)
        for target, saved, existed in records:
            if existed:
                shutil.copy2(saved, target)
            else:
                target.unlink(missing_ok=True)
        if not was_enabled:
            subprocess.run(["systemctl", "disable", service], check=False)
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        if was_active:
            subprocess.run(["systemctl", "start", service], check=False)
        raise


def interrupted(signum, _frame):
    raise InterruptedError(f"Interrumpido por señal {signum}")


def main():
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    plan_path = Path(sys.argv[1])
    stage = plan_path.parent
    if stage.parent != Path("/tmp") or not stage.name.startswith("studio-"):
        sys.exit("Staging inválido")
    try:
        apply(json.loads(plan_path.read_text(encoding="utf-8")), stage)
    except Exception as exc:
        sys.exit(f"Error al aplicar servicio: {exc}")
    finally:
        shutil.rmtree(stage)


if __name__ == "__main__":
    main()
