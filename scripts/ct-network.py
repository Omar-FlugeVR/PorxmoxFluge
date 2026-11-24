#!/usr/bin/env python3
"""Convierte la red validada a opciones de pct o la comprueba contra un CT."""

import argparse
from pathlib import Path
import sys

from inventory import CT_ROLES, ConfigError, role_networks, settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("id")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true", help="Lee pct config por stdin")
    mode.add_argument("--check-bridges", action="store_true")
    args = parser.parse_args()
    try:
        cfg = settings()
        role = next((role for role in CT_ROLES if cfg[f"{role.upper()}_CT_ID"] == args.id
                     and cfg[f"{role.upper()}_IP"]), None)
        if role is None:
            raise ConfigError(f"CT {args.id} sin red configurada")
        nics = role_networks(cfg, role)
        if args.check_bridges:
            for nic in nics:
                if not (Path("/sys/class/net") / nic["bridge"] / "bridge").is_dir():
                    raise ConfigError(f"No existe el puente Linux {nic['bridge']}")
        elif args.verify:
            verify(args.id, nics, explicit=bool(cfg[f"{role.upper()}_NICS"]))
        else:
            for index, nic in enumerate(nics):
                options = [f"name=eth{index}", f"bridge={nic['bridge']}", f"ip={nic['ip']}", "ip6=manual"]
                if nic["gateway"]:
                    options.append(f"gw={nic['gateway']}")
                if nic["vlan"]:
                    options.append(f"tag={nic['vlan']}")
                print(",".join(options))
    except ConfigError as exc:
        parser.exit(1, f"Error: {exc}\n")


def verify(ct_id, nics, explicit):
    cfg = dict(line.rstrip("\n").split(": ", 1) for line in sys.stdin if ": " in line)
    if cfg.get("unprivileged") != "1":
        raise ConfigError(f"CT {ct_id}: se requiere un CT no privilegiado")
    for index, nic in enumerate(nics):
        key = f"net{index}"
        actual = dict(field.split("=", 1) for field in cfg.get(key, "").split(",") if "=" in field)
        expected = {"ip": nic["ip"], "bridge": nic["bridge"]}
        if explicit:
            expected.update(name=f"eth{index}", gw=nic["gateway"], tag=nic["vlan"])
        if any(actual.get(k, "") != value for k, value in expected.items()):
            raise ConfigError(f"CT {ct_id}: {key} no coincide con config.env")
    if explicit and any(f"net{index}" in cfg for index in range(len(nics), 32)):
        raise ConfigError(f"CT {ct_id}: hay NIC adicionales; actualiza config.env")


if __name__ == "__main__":
    main()
