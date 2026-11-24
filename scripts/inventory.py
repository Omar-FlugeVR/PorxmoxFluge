#!/usr/bin/env python3
"""Inventario validado y configuración compartida. Solo biblioteca estándar."""

import ipaddress
import json
import os
from pathlib import Path
import re
from string import Template
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    pass


LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
IP_VARS = ("PROXMOX_IP", "ADGUARD_IP", "CADDY_IP", "COMPANION_IP", "NDI_IP")
CT_ROLES = ("adguard", "caddy", "companion", "ndi")
PORT_VARS = ("COMPANION_PORT", "NDI_PORT")
DEFAULT_NDI_INSTALLER_URL = "https://downloads.ndi.tv/SDK/NDI_SDK_Linux/Install_NDI_SDK_v6_Linux.tar.gz"


def ipv4(value, label):
    try:
        addr = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ConfigError(f"{label}: dirección IPv4 inválida: {value!r}") from exc
    if addr.is_unspecified or addr.is_multicast or addr.is_loopback or addr.is_reserved:
        raise ConfigError(f"{label}: se requiere una IPv4 unicast de la red")
    return addr


def nic_settings(nic, label):
    if not isinstance(nic, dict) or set(nic) != {"bridge", "ip", "gateway", "vlan"}:
        raise ConfigError(f"{label}: se esperan bridge, ip, gateway y vlan")
    if any(not isinstance(value, str) for value in nic.values()):
        raise ConfigError(f"{label}: los valores deben ser texto")
    if not TOKEN.fullmatch(nic["bridge"]):
        raise ConfigError(f"{label}: puente inválido")
    address, slash, prefix = nic["ip"].partition("/")
    if not slash or not re.fullmatch(r"(?:[1-9]|[12][0-9]|30)", prefix):
        raise ConfigError(f"{label}: escribe la IP con su prefijo CIDR entre 1 y 30")
    host = ipv4(address, label)
    network = ipaddress.IPv4Network(nic["ip"], strict=False)
    if host in (network.network_address, network.broadcast_address):
        raise ConfigError(f"{label}: la IP debe ser utilizable en {network}")
    if nic["gateway"]:
        gateway = ipv4(nic["gateway"], f"{label} gateway")
        if gateway not in network or gateway in (network.network_address, network.broadcast_address, host):
            raise ConfigError(f"{label}: la puerta de enlace debe ser otra IP utilizable en {network}")
    if nic["vlan"] and (not re.fullmatch(r"[1-9][0-9]{0,3}", nic["vlan"]) or int(nic["vlan"]) > 4094):
        raise ConfigError(f"{label}: la VLAN debe estar entre 1 y 4094")
    return nic


def role_networks(cfg, role):
    key = role.upper()
    if cfg[f"{key}_NICS"]:
        try:
            nics = json.loads(cfg[f"{key}_NICS"])
        except ValueError as exc:
            raise ConfigError(f"{key}_NICS no contiene JSON válido") from exc
        if not isinstance(nics, list) or not 1 <= len(nics) <= 32:
            raise ConfigError(f"{key}_NICS debe contener entre 1 y 32 NIC")
        nics = [nic_settings(nic, f"{role} eth{index}") for index, nic in enumerate(nics)]
    else:
        nics = [nic_settings({"bridge": cfg["BRIDGE"], "ip": f"{cfg[f'{key}_IP']}/{cfg['CIDR']}",
                              "gateway": cfg["GATEWAY"], "vlan": ""}, f"{role} eth0")]
    if nics[0]["ip"].split("/")[0] != cfg[f"{key}_IP"]:
        raise ConfigError(f"{key}_IP debe coincidir con la IPv4 de eth0")
    if sum(bool(nic["gateway"]) for nic in nics) > 1:
        raise ConfigError(f"{role}: solo una NIC puede tener puerta de enlace")
    return nics


def settings(environ=None):
    env = os.environ if environ is None else environ
    required = ("PROXMOX_IP", "TEMPLATE_STORAGE",
                "DISK_STORAGE", "BASE_DOMAIN", "LANDING_SUBDOMAIN",
                *(f"{role.upper()}_CT_ID" for role in CT_ROLES),
                *(f"{role.upper()}_HOSTNAME" for role in CT_ROLES),
                *PORT_VARS, "COMPANION_BUILD", "BOOTSTRAP_DNS", "UPSTREAM_DNS",
                "TIMEZONE", "ADGUARD_ADMIN_USER")
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise ConfigError("Faltan variables: " + ", ".join(missing))
    cfg = {key: env[key] for key in required}
    for key in (*IP_VARS[1:], "GATEWAY", "CIDR", "BRIDGE", *(f"{role.upper()}_NICS" for role in CT_ROLES)):
        cfg[key] = env.get(key, "")
    configured = [role for role in CT_ROLES if cfg[f"{role.upper()}_IP"]]
    if not configured:
        raise ConfigError("Configura al menos una IP de CT")
    selection = env.get("INSTALL_CTS", " ".join(configured)).split()
    if not selection or len(set(selection)) != len(selection) or any(role not in configured for role in selection):
        raise ConfigError("INSTALL_CTS debe enumerar CTs configurados y sin duplicados")
    cfg["INSTALL_CTS"] = " ".join(selection)
    cfg.update({key: env.get(key, default) for key, default in (
        ("NDI_INSTALLER_URL", DEFAULT_NDI_INSTALLER_URL),
        ("NDI_INSTALLER_FILE", ""), ("NDI_INSTALLER_SHA256", ""), ("NDI_LICENSE_SHA256", ""))})
    cfg["BASE_DOMAIN"] = cfg["BASE_DOMAIN"].lstrip(".").lower().rstrip(".")
    domain = cfg["BASE_DOMAIN"]
    if len(domain) > 190 or any(not LABEL.fullmatch(part) for part in domain.split(".")):
        raise ConfigError("BASE_DOMAIN no es un dominio válido")
    for key in ("LANDING_SUBDOMAIN", *(f"{role.upper()}_HOSTNAME" for role in CT_ROLES)):
        if not LABEL.fullmatch(cfg[key]):
            raise ConfigError(f"{key}: nombre inválido")
    for key in ("TEMPLATE_STORAGE", "DISK_STORAGE"):
        if not TOKEN.fullmatch(cfg[key]):
            raise ConfigError(f"{key}: identificador inválido")
    host = ipv4(cfg["PROXMOX_IP"], "PROXMOX_IP")
    ipv4(cfg["BOOTSTRAP_DNS"], "BOOTSTRAP_DNS")
    ct_ips = set()
    gateways = set()
    for role in configured:
        for nic in role_networks(cfg, role):
            address = nic["ip"].split("/")[0]
            if address == str(host) or address in ct_ips:
                raise ConfigError(f"{role}: IP repetida entre el host y los CTs: {address}")
            ct_ips.add(address)
            if nic["gateway"]:
                gateways.add(nic["gateway"])
        if not cfg[f"{role.upper()}_NICS"]:
            net = ipaddress.IPv4Network(f"{cfg['PROXMOX_IP']}/{cfg['CIDR']}", strict=False)
            if host in (net.network_address, net.broadcast_address) or ipaddress.IPv4Address(cfg[f"{role.upper()}_IP"]) not in net:
                raise ConfigError(f"{role}: con la red compartida, la IP debe estar en {net}")
    if (ct_ips | {str(host)}) & gateways:
        raise ConfigError("La puerta de enlace no puede ser la IP de Proxmox ni de un CT")
    if cfg["BOOTSTRAP_DNS"] in ct_ips:
        raise ConfigError("BOOTSTRAP_DNS debe ser independiente de los cuatro CTs que se van a crear")
    upstreams = cfg["UPSTREAM_DNS"].split()
    if not upstreams:
        raise ConfigError("UPSTREAM_DNS necesita al menos un servidor DNS IPv4")
    for server in upstreams:
        ipv4(server, "UPSTREAM_DNS")
        if server in ct_ips:
            raise ConfigError("UPSTREAM_DNS no puede apuntar a ninguno de estos CTs")
    ct_ids = [f"{role.upper()}_CT_ID" for role in CT_ROLES]
    for key in ct_ids:
        if not re.fullmatch(r"[1-9][0-9]{2,8}", cfg[key]):
            raise ConfigError(f"{key}: usa un ID entre 100 y 999999999, sin ceros iniciales")
    if len({cfg[key] for key in ct_ids}) != len(ct_ids):
        raise ConfigError("Los CT IDs deben ser distintos")
    if len({cfg[f"{role.upper()}_HOSTNAME"] for role in CT_ROLES}) != len(CT_ROLES):
        raise ConfigError("Los cuatro CTs necesitan hostnames distintos")
    for key in PORT_VARS:
        if not cfg[key].isascii() or not cfg[key].isdigit() or not 1024 <= int(cfg[key]) <= 65535:
            raise ConfigError(f"{key}: usa un puerto entre 1024 y 65535")
    if not re.fullmatch(r"stable|[0-9]+\.[0-9]+\.[0-9]+", cfg["COMPANION_BUILD"]):
        raise ConfigError("COMPANION_BUILD debe ser stable o una versión estable x.y.z")
    for key in ("NDI_INSTALLER_SHA256", "NDI_LICENSE_SHA256"):
        if cfg[key] and not re.fullmatch(r"[a-fA-F0-9]{64}", cfg[key]):
            raise ConfigError(f"{key} debe tener 64 dígitos hexadecimales")
        cfg[key] = cfg[key].lower()
    if not cfg["NDI_INSTALLER_FILE"]:
        validate_target(cfg["NDI_INSTALLER_URL"], "NDI_INSTALLER_URL")
        if not cfg["NDI_INSTALLER_URL"].startswith("https://"):
            raise ConfigError("NDI_INSTALLER_URL debe usar HTTPS")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", cfg["ADGUARD_ADMIN_USER"]):
        raise ConfigError("ADGUARD_ADMIN_USER inválido")
    try:
        ZoneInfo(cfg["TIMEZONE"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError("TIMEZONE no es una zona IANA instalada") from exc
    return cfg


def expand(value, cfg):
    if not isinstance(value, str):
        raise ConfigError("Los valores del inventario deben ser cadenas de texto")
    try:
        return Template(value).substitute({key: cfg[key] for key in (*IP_VARS, *PORT_VARS)})
    except (KeyError, ValueError) as exc:
        raise ConfigError(f"Variable desconocida o sintaxis inválida en {value!r}") from exc


def uses_missing_ct(value, cfg):
    return any(not cfg[key] and re.search(r"\$\{" + key + r"\}|\$" + key + r"\b", value)
               for key in IP_VARS[1:])


def validate_target(target, fqdn):
    # Excluir metacaracteres de Caddy, placeholders, credenciales e inyección de líneas.
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in target):
        raise ConfigError(f"{fqdn}: target contiene espacios o caracteres de control")
    if any(char in target for char in '{}"\\`<>'):
        raise ConfigError(f"{fqdn}: target contiene metacaracteres no permitidos")
    try:
        parsed = urlsplit(target)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{fqdn}: URL destino inválida") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None:
        raise ConfigError(f"{fqdn}: target debe ser una URL http(s) absoluta sin credenciales")
    if parsed.password is not None or (port is not None and not 1 <= port <= 65535):
        raise ConfigError(f"{fqdn}: puerto o credenciales inválidos")
    if parsed.query or parsed.fragment or "?" in target or "#" in target:
        raise ConfigError(f"{fqdn}: target debe ser una URL base sin query ni fragmento")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if any(not LABEL.fullmatch(part) for part in parsed.hostname.lower().rstrip(".").split(".")):
            raise ConfigError(f"{fqdn}: host destino inválido") from None
    if parsed.hostname.lower().rstrip(".") == fqdn:
        raise ConfigError(f"{fqdn}: target provocaría una redirección a sí mismo")
    return target.rstrip("/")


def load_services(path, cfg, allow_empty=False):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"No se puede leer el inventario {path}: {exc}") from exc
    if not isinstance(data, list) or (not data and not allow_empty):
        raise ConfigError("services.json debe contener una lista no vacía")
    seen = {cfg["LANDING_SUBDOMAIN"]}
    services = []
    for index, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise ConfigError(f"Servicio {index}: se esperaba un objeto")
        for key in ("name", "subdomain", "target", "category", "description", "mode"):
            if key not in item or not isinstance(item[key], str):
                raise ConfigError(f"Servicio {index}: falta el texto {key}")
        for key in ("name", "category", "description"):
            if not item[key].strip() or any(ord(c) < 32 for c in item[key]):
                raise ConfigError(f"Servicio {index}: {key} vacío o con caracteres de control")
        sub = item["subdomain"]
        if not LABEL.fullmatch(sub) or sub in seen:
            raise ConfigError(f"Subdominio inválido, duplicado o reservado: {sub!r}")
        seen.add(sub)
        service = dict(item)
        service["fqdn"] = f"{sub}.{cfg['BASE_DOMAIN']}"
        if item["mode"] == "redirect":
            if not allow_empty and uses_missing_ct(item["target"], cfg):
                continue
            service["target"] = validate_target(expand(item["target"], cfg), service["fqdn"])
            if urlsplit(service["target"]).hostname == cfg["CADDY_IP"]:
                raise ConfigError(f"{sub}: target no debe apuntar al propio Caddy")
        elif item["mode"] == "dns_only":
            address = item.get("ip", "")
            if not allow_empty and isinstance(address, str) and uses_missing_ct(address, cfg):
                continue
            service["ip"] = expand(address, cfg)
            ipv4(service["ip"], f"{sub}.ip")
            if item["target"]:
                raise ConfigError(f"{sub}: usa target vacío para servicios sin interfaz HTTP")
        else:
            raise ConfigError(f"{sub}: mode debe ser redirect o dns_only")
        services.append(service)
    return services
