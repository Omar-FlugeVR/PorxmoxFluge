#!/usr/bin/env python3
"""Configuración de arranque de Companion; no modifica su base de datos."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from inventory import ConfigError, load_services, settings


def render(cfg, _services):
    override = (Path(__file__).parent / "templates/override.conf").read_text(encoding="utf-8")
    plan = {
        "service": "companion",
        "verify_unit": "/etc/systemd/system/companion.service",
        "files": [{"source": "companion-override.conf", "target": "/etc/systemd/system/companion.service.d/10-studio-infra.conf"}],
        "health": {"kind": "http", "host": "127.0.0.1", "port": int(cfg["COMPANION_PORT"])},
    }
    return {"companion-override.conf": override.format(port=int(cfg["COMPANION_PORT"])),
            "apply.json": json.dumps(plan, ensure_ascii=False, indent=2) + "\n"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services", default="services.json")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        cfg = settings()
        output = Path(args.output_dir)
        artifacts = render(cfg, load_services(args.services, cfg))
        output.mkdir(parents=True, exist_ok=True)
        for name, content in artifacts.items():
            (output / name).write_text(content, encoding="utf-8")
    except (ConfigError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
