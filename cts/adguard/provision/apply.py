#!/usr/bin/env python3
"""Aplica las reescrituras dentro del CT de AdGuard. Actualización transaccional."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import yaml


CONFIG = Path("/opt/AdGuardHome/AdGuardHome.yaml")
STATE = Path("/var/lib/studio-infra/managed-rewrites.json")
BINARY = "/opt/AdGuardHome/AdGuardHome"
BACKUP_ROOT = Path("/var/backups/studio-infra/adguard")


def merge_rewrites(config, previous, desired):
    """Reemplaza solo los dominios gestionados, incluidos los que se han eliminado."""
    filtering = config.setdefault("filtering", {})
    if not isinstance(filtering, dict):
        raise ValueError("filtering no es un mapa YAML")
    if filtering.get("rewrites_enabled") is False:
        raise ValueError("Las reescrituras DNS están desactivadas globalmente en AdGuard; actívalas antes de sincronizar")
    current = filtering.get("rewrites") or []
    if not isinstance(current, list) or any(not isinstance(rule, dict) for rule in current):
        raise ValueError("filtering.rewrites no es una lista de reglas")
    owned = {rule["domain"].lower().rstrip(".") for rule in previous + desired}
    kept = [rule for rule in current if str(rule.get("domain", "")).lower().rstrip(".") not in owned]
    filtering["rewrites"] = kept + desired
    return config


def run(*args):
    subprocess.run(args, check=True)


def active():
    return subprocess.run(["systemctl", "is-active", "--quiet", "AdGuardHome"], check=False).returncode == 0


def wait_active():
    # Dar margen para detectar errores de arranque que llegan después de systemctl start.
    for _ in range(5):
        time.sleep(1)
        if not active():
            raise RuntimeError("AdGuardHome se ha detenido al aplicar la configuración")


def atomic_write(path, content):
    fd, filename = tempfile.mkstemp(prefix=".studio-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
        os.chmod(filename, 0o600)
        os.replace(filename, path)
    finally:
        Path(filename).unlink(missing_ok=True)


def interrupted(signum, _frame):
    raise InterruptedError(f"Interrumpido por señal {signum}")


def apply(plan):
    desired = plan["rewrites"]
    if not isinstance(desired, list):
        raise ValueError("Plan de rewrites inválido")
    previous = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else []
    before = CONFIG.read_text(encoding="utf-8")
    cfg = yaml.safe_load(before)
    if not isinstance(cfg, dict) or not cfg.get("users"):
        raise ValueError("AdGuard necesita un YAML inicializado con autenticación; no se ejecuta su asistente sobre CTs existentes")
    original_rules = (cfg.get("filtering") or {}).get("rewrites") or []
    if not previous and not desired:
        if not active():
            run("systemctl", "start", "AdGuardHome")
            wait_active()
        print("[OK] AdGuard sin reescrituras gestionadas.")
        return
    merge_rewrites(cfg, previous, desired)
    if original_rules == cfg["filtering"]["rewrites"] and previous == desired:
        if not active():
            run("systemctl", "start", "AdGuardHome")
            wait_active()
        print("[OK] AdGuard sin cambios.")
        return
    backup_root = BACKUP_ROOT
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix="change-", dir=backup_root))
    was_active = active()
    state_existed = STATE.exists()
    stopped = False
    saved = False
    try:
        run("systemctl", "stop", "AdGuardHome")
        stopped = True
        # AdGuard guarda ajustes al detenerse; la lectura autoritativa debe ser posterior.
        shutil.copy2(CONFIG, backup / "AdGuardHome.yaml")
        (backup / "AdGuardHome.yaml").chmod(0o600)
        if state_existed:
            shutil.copy2(STATE, backup / "managed-rewrites.json")
        saved = True
        cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        merge_rewrites(cfg, previous, desired)
        candidate = CONFIG.parent / ".AdGuardHome.studio-new.yaml"
        atomic_write(candidate, yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
        try:
            run(BINARY, "--check-config", "-c", str(candidate), "-w", str(CONFIG.parent))
            os.replace(candidate, CONFIG)
        finally:
            candidate.unlink(missing_ok=True)
        run("systemctl", "start", "AdGuardHome")
        wait_active()
        STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write(STATE, json.dumps(desired, ensure_ascii=False, indent=2) + "\n")
        print(f"[OK] Copia anterior de AdGuard: {backup}")
    except BaseException:
        # Incluso Ctrl+C debe restaurar YAML y manifest, evitando perder el DNS.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print(f"[ERROR] Restaurando AdGuard desde {backup}", file=sys.stderr)
        if stopped:
            subprocess.run(["systemctl", "stop", "AdGuardHome"], check=False)
        if saved:
            shutil.copy2(backup / "AdGuardHome.yaml", CONFIG)
            if state_existed:
                shutil.copy2(backup / "managed-rewrites.json", STATE)
            else:
                STATE.unlink(missing_ok=True)
        if was_active:
            subprocess.run(["systemctl", "start", "AdGuardHome"], check=False)
        raise


def main():
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    plan_path = Path(sys.argv[1])
    stage = plan_path.parent
    if stage.parent != Path("/tmp") or not stage.name.startswith("studio-adguard."):
        sys.exit("Staging inválido")
    try:
        apply(json.loads(plan_path.read_text(encoding="utf-8")))
    except Exception as exc:
        sys.exit(f"Error al sincronizar AdGuard: {exc}")
    finally:
        shutil.rmtree(stage)


if __name__ == "__main__":
    main()
