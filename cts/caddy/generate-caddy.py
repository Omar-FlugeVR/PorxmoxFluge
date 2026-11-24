#!/usr/bin/env python3
"""Genera únicamente sitios HTTP y redirecciones explícitas 302."""

import argparse
from pathlib import Path
import sys

# Permitir ejecución directa desde cualquier directorio, sin instalar paquetes.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from inventory import ConfigError, load_services, settings


def render(cfg, services):
    lines = [
        "# Generado desde services.json. Se reemplaza al ejecutar sync.sh.",
        "{", "\tauto_https off", "}", "",
        f"http://{cfg['LANDING_SUBDOMAIN']}.{cfg['BASE_DOMAIN']}, http://{cfg['CADDY_IP']} {{",
        "\troot * /opt/fluge-control/public",
        "\tencode gzip",
        "\theader X-Content-Type-Options nosniff",
        "\theader Referrer-Policy same-origin",
        "\theader X-Frame-Options DENY",
        "\theader Cache-Control no-cache",
        '\theader Content-Security-Policy "default-src \'self\'; script-src \'self\'; style-src \'self\'; img-src \'self\' data:; connect-src \'self\'; frame-ancestors \'none\'; base-uri \'self\'; form-action \'self\'"',
        "\thandle /api.php {",
        "\t\tphp_fastcgi unix//run/php/fluge-control.sock",
        "\t}",
        "\thandle /api/ndi/status {",
        "\t\trewrite * /api.php?ndi_action=ndi_status",
        "\t\tphp_fastcgi unix//run/php/fluge-control.sock",
        "\t}",
        "\thandle /api/ndi/test {",
        "\t\trewrite * /api.php?ndi_action=ndi_test",
        "\t\tphp_fastcgi unix//run/php/fluge-control.sock",
        "\t}",
        "\thandle /api/ndi/log/clear {",
        "\t\trewrite * /api.php?ndi_action=ndi_clear_log",
        "\t\tphp_fastcgi unix//run/php/fluge-control.sock",
        "\t}",
        "\thandle {",
        "\t\t@otherPhp path *.php",
        "\t\trespond @otherPhp 404",
        "\t\tfile_server",
        "\t}",
        "}", "", "import /etc/caddy/studio-routes.caddy", "",
    ]
    return "\n".join(lines)


def routes(cfg, services):
    lines = ["# Gestionado por Servicio de control.", ""]
    for service in services:
        if service["mode"] == "redirect":
            lines += [f"http://{service['fqdn']} {{",
                      f'\tredir "{service["target"]}{{uri}}" 302', "}", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services", default="services.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        cfg = settings()
        Path(args.output).write_text(render(cfg, load_services(args.services, cfg)), encoding="utf-8")
    except (ConfigError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
