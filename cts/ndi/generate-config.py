#!/usr/bin/env python3
"""Configuración y unidad systemd de NDI Discovery Service."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from inventory import ConfigError, load_services, settings


def render(cfg, _services):
    unit = (Path(__file__).parent / "templates/ndi-discovery.service").read_text(encoding="utf-8")
    config = {"binding": cfg["NDI_IP"], "port_no": str(int(cfg["NDI_PORT"]))}
    plan = {
        "service": "ndi-discovery",
        "verify_unit": "/etc/systemd/system/ndi-discovery.service",
        "files": [
            {"source": "ndi-discovery.service", "target": "/etc/systemd/system/ndi-discovery.service"},
            {"source": "ndi-discovery-service.v1.json", "target": "/etc/ndi/ndi-discovery-service.v1.json"},
        ],
        "health": {"kind": "tcp", "host": cfg["NDI_IP"], "port": int(cfg["NDI_PORT"])},
    }
    return {"ndi-discovery.service": unit,
            "ndi-discovery-service.v1.json": json.dumps(config, indent=2) + "\n",
            "apply.json": json.dumps(plan, ensure_ascii=False, indent=2) + "\n"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services", default="services.json")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        cfg = settings()
        artifacts = render(cfg, load_services(args.services, cfg))
        output = Path(args.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        for name, content in artifacts.items():
            (output / name).write_text(content, encoding="utf-8")
    except (ConfigError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
