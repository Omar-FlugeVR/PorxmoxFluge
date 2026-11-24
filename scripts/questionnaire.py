#!/usr/bin/env python3
"""Asistente de instalación. Escribe un candidato a config.env en staging."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

from inventory import CT_ROLES, ConfigError, LABEL, TOKEN, ipv4, nic_settings, role_networks, settings


NAMES = {"adguard": "AdGuard Home", "caddy": "Caddy / panel Fluge",
         "companion": "Bitfocus Companion", "ndi": "NDI Discovery Service"}
ROOT = Path(__file__).resolve().parents[1]


def ask(label, default="", allow_empty=False, valid=lambda _: True):
    while True:
        hint = f" [{default}]" if default else ""
        value = input(f"{label}{hint}: ").strip()
        if allow_empty and value == "-":
            return ""
        value = value or default
        if (value or allow_empty) and valid(value):
            return value
        print("Valor inválido. Prueba de nuevo.")


def yes(label, default):
    value = ask(label + " (s/n)", "s" if default else "n",
                valid=lambda answer: answer.lower() in {"s", "si", "sí", "n", "no"})
    return value.lower() in {"s", "si", "sí"}


def valid_dns(value, multiple=False):
    addresses = value.split()
    if not addresses or (not multiple and len(addresses) != 1):
        return False
    try:
        for address in addresses:
            ipv4(address, "DNS")
    except ConfigError:
        return False
    return True


def host_network():
    addresses = {}
    for link in json.loads(subprocess.check_output(["ip", "-j", "-4", "addr", "show", "scope", "global"])):
        for item in link.get("addr_info", []):
            addresses[item["local"]] = (link["ifname"], item["prefixlen"])
    bridges = sorted(path.name for path in Path("/sys/class/net").iterdir() if (path / "bridge").is_dir())
    if not addresses or not bridges:
        raise ConfigError("Proxmox necesita una IPv4 y al menos un puente Linux activo")
    routes = json.loads(subprocess.check_output(["ip", "-j", "-4", "route", "show", "default"]))
    route = next((route for route in routes if route.get("gateway")), {})
    return addresses, bridges, route


def ask_nics(role, previous, default_bridge, host_gateway, bridges):
    count = int(ask("Número de NIC", str(len(previous) or 1),
                    valid=lambda value: bool(re.fullmatch(r"(?:[1-9]|[12][0-9]|3[0-2])", value))))
    nics = []
    for index in range(count):
        old = previous[index] if index < len(previous) else {}
        label = f"{role} eth{index}"
        while True:
            bridge = ask(f"{label} · puente ({', '.join(bridges)})",
                         old.get("bridge", default_bridge), valid=lambda value: value in bridges)
            address = ask(f"{label} · IPv4/CIDR", old.get("ip", ""))
            vlan = ask(f"{label} · VLAN (- para ninguna)", old.get("vlan", ""), allow_empty=True)
            gateway = ""
            if not any(nic["gateway"] for nic in nics):
                suggested = old.get("gateway", "")
                if not old and index == 0 and host_gateway:
                    try:
                        if ipaddress.IPv4Address(host_gateway) in ipaddress.IPv4Network(address, strict=False):
                            suggested = host_gateway
                    except ValueError:
                        pass
                gateway = ask(f"{label} · puerta de enlace IPv4 (- para ninguna)", suggested, allow_empty=True)
            try:
                nic = nic_settings(dict(bridge=bridge, ip=address, gateway=gateway, vlan=vlan), label)
            except ConfigError as exc:
                print(exc)
                continue
            nics.append(nic)
            break
    return nics


def questionnaire(destination, candidates, existing):
    defaults = {}
    for line in (ROOT / "config.env.example").read_text(encoding="utf-8").splitlines():
        fields = shlex.split(line, comments=True)
        if fields:
            key, value = fields[0].split("=", 1)
            defaults[key] = os.environ.get(key, value) if existing else value
    for role in CT_ROLES:
        key = f"{role.upper()}_NICS"
        defaults[key] = os.environ.get(key, "") if existing else ""
        if not existing or f"{role.upper()}_IP" not in os.environ:
            defaults[f"{role.upper()}_IP"] = ""

    saved = os.environ.get("INSTALL_CTS", " ".join(CT_ROLES)).split() if existing else candidates
    chosen = [role for role in candidates if yes(f"¿Instalar {NAMES[role]}?", role in saved or len(candidates) == 1)]
    if not chosen:
        print("No se ha seleccionado ningún CT.")
        return

    addresses, bridges, route = host_network()
    suggested = next((ip for ip, (device, _) in addresses.items() if device == route.get("dev")), next(iter(addresses)))
    cfg = defaults.copy()
    cfg["PROXMOX_IP"] = ask(f"IP de Proxmox ({', '.join(addresses)})",
                           defaults["PROXMOX_IP"] if existing and defaults["PROXMOX_IP"] in addresses else suggested,
                           valid=lambda value: value in addresses)
    host_bridge, _ = addresses[cfg["PROXMOX_IP"]]
    default_bridge = host_bridge if host_bridge in bridges else bridges[0]
    cfg["TEMPLATE_STORAGE"] = ask("Storage de plantillas", cfg["TEMPLATE_STORAGE"], valid=TOKEN.fullmatch)
    cfg["DISK_STORAGE"] = ask("Storage de discos de CT", cfg["DISK_STORAGE"], valid=TOKEN.fullmatch)
    cfg["BOOTSTRAP_DNS"] = ask("DNS IPv4 para instalar los CTs", cfg["BOOTSTRAP_DNS"], valid=valid_dns)
    if "adguard" in chosen:
        cfg["UPSTREAM_DNS"] = ask("DNS upstream de AdGuard (separados por espacios)", cfg["UPSTREAM_DNS"],
                                  valid=lambda value: valid_dns(value, multiple=True))
    if "adguard" in chosen or "caddy" in chosen:
        cfg["BASE_DOMAIN"] = ask("Dominio local", cfg["BASE_DOMAIN"])
    if "caddy" in chosen:
        cfg["LANDING_SUBDOMAIN"] = ask("Nombre DNS del panel", cfg["LANDING_SUBDOMAIN"], valid=LABEL.fullmatch)

    for role in chosen:
        key = role.upper()
        print(f"\n{NAMES[role]}")
        cfg[f"{key}_CT_ID"] = ask("ID de CT", cfg[f"{key}_CT_ID"],
                                 valid=lambda value: bool(re.fullmatch(r"[1-9][0-9]{2,8}", value)))
        cfg[f"{key}_HOSTNAME"] = ask("Hostname", cfg[f"{key}_HOSTNAME"], valid=LABEL.fullmatch)
        previous = role_networks(defaults, role) if defaults[f"{key}_IP"] else []
        nics = ask_nics(role, previous, default_bridge, route.get("gateway"), bridges)
        cfg[f"{key}_IP"] = nics[0]["ip"].split("/")[0]
        cfg[f"{key}_NICS"] = json.dumps(nics, separators=(",", ":"))

    cfg["INSTALL_CTS"] = " ".join(chosen)
    cfg = settings(cfg)
    print("\nRed prevista")
    for role in chosen:
        print(f"  {NAMES[role]} · CT {cfg[role.upper() + '_CT_ID']}")
        for index, nic in enumerate(role_networks(cfg, role)):
            print(f"    eth{index} · {nic['bridge']} · {nic['ip']} · VLAN {nic['vlan'] or 'ninguna'}"
                  f" · gateway {nic['gateway'] or 'ninguno'}")
    print(f"  Storage de plantillas {cfg['TEMPLATE_STORAGE']} · discos {cfg['DISK_STORAGE']}")
    print(f"  DNS de instalación {cfg['BOOTSTRAP_DNS']} · dominio {cfg['BASE_DOMAIN']}")
    with destination.open("x", encoding="utf-8", newline="\n") as output:
        output.write("# Configuración del asistente de instalación de Fluge.\n")
        for key, value in cfg.items():
            output.write(f"{key}={shlex.quote(value)}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("roles", nargs="+", choices=CT_ROLES)
    parser.add_argument("--existing", action="store_true")
    args = parser.parse_args()
    try:
        questionnaire(args.destination, args.roles, args.existing)
    except EOFError:
        parser.exit(1, "Cuestionario cancelado.\n")
    except (ConfigError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
