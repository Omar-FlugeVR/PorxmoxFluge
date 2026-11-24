#!/usr/bin/env python3
"""Valida configuración/inventario y genera todos los artefactos locales."""

import argparse
import importlib.util
import json
from pathlib import Path

from inventory import CT_ROLES, ConfigError, load_services, settings


def renderer(relative_path, function="render"):
    path = Path(__file__).resolve().parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, function)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services", default="services.json")
    parser.add_argument("--output-dir")
    parser.add_argument("--ct", choices=("all", *CT_ROLES), default="all")
    parser.add_argument("--runtime", action="store_true", help="Aceptar un inventario vacío exportado por el panel")
    parser.add_argument("--config-only", action="store_true", help="Validar solo config.env, sin leer el inventario inicial")
    args = parser.parse_args()
    try:
        cfg = settings()
        if args.config_only:
            print(f"config.env válido: dominio {cfg['BASE_DOMAIN']}.")
            return
        roles = cfg["INSTALL_CTS"].split() if args.ct == "all" else [args.ct]
        for role in roles:
            if not cfg[f"{role.upper()}_IP"]:
                raise ConfigError(f"El CT {role} no tiene IP configurada")
        # Solo Caddy y AdGuard consumen el inventario del panel o la semilla.
        uses_inventory = "caddy" in roles or "adguard" in roles
        services = load_services(args.services, cfg, allow_empty=args.runtime) if uses_inventory else []
        if args.output_dir:
            root = Path(args.output_dir)
            if "caddy" in roles:
                output = root / "caddy"
                output.mkdir(parents=True, exist_ok=True)
                (output / "Caddyfile").write_text(renderer("cts/caddy/generate-caddy.py")(cfg, services), encoding="utf-8")
                (output / "index.html").write_text(renderer("cts/caddy/generate-landing.py")(cfg, services), encoding="utf-8")
                (output / "routes.caddy").write_text(renderer("cts/caddy/generate-caddy.py", "routes")(cfg, services), encoding="utf-8")
                (output / "seed.json").write_text(json.dumps(renderer("cts/caddy/generate-seed.py")(cfg, services), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if "adguard" in roles:
                output = root / "adguard"
                output.mkdir(parents=True, exist_ok=True)
                plan = renderer("cts/adguard/generate-rewrites.py")(cfg, services)
                (output / "rewrites.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            for role in ("companion", "ndi"):
                if role in roles:
                    output = root / role
                    output.mkdir(parents=True, exist_ok=True)
                    artifacts = renderer(f"cts/{role}/generate-config.py")(cfg, services)
                    for filename, content in artifacts.items():
                        (output / filename).write_text(content, encoding="utf-8")
        scope = f"{len(services)} servicios" if uses_inventory else "CT " + ", ".join(roles)
        print(f"Configuración válida: {scope}, dominio {cfg['BASE_DOMAIN']}.")
    except (ConfigError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
