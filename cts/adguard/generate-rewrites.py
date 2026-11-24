#!/usr/bin/env python3
"""Genera las reescrituras DNS del CT de AdGuard a partir del inventario único."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from inventory import ConfigError, load_services, settings


def render(cfg, services):
    rules = []
    if cfg["CADDY_IP"]:
        rules.append({"domain": f"*.{cfg['BASE_DOMAIN']}", "answer": cfg["CADDY_IP"], "enabled": True})
    rules.extend({"domain": item["fqdn"], "answer": item["ip"], "enabled": True}
                 for item in services if item["mode"] == "dns_only")
    return {"rewrites": rules}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services", default="services.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        cfg = settings()
        plan = render(cfg, load_services(args.services, cfg))
        Path(args.output).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ConfigError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
