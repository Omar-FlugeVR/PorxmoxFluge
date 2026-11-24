#!/usr/bin/env python3
"""Landing estática autocontenida, accesible y sin dependencias externas."""

import argparse
import base64
from collections import OrderedDict
from html import escape
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from inventory import ConfigError, load_services, settings


def render(cfg, services):
    groups = OrderedDict()
    for item in services:
        groups.setdefault(item["category"], []).append(item)
    sections = []
    for number, (category, items) in enumerate(groups.items()):
        cards = []
        for item in items:
            fqdn = escape(item["fqdn"], quote=True)
            search = escape(" ".join(str(item.get(k, "")) for k in
                                    ("name", "category", "description", "fqdn", "target", "ip")), quote=True)
            if item["mode"] == "redirect":
                target = escape(item["target"], quote=True)
                action = (f'<a class="open" href="{target}">Abrir servicio</a>'
                          f'<a class="shortcut" href="http://{fqdn}/">{fqdn}</a>')
                kind = '<span class="access-type">Interfaz web</span>'
                endpoint = target
            else:
                action = f'<span class="dns-note">DNS directo · sin interfaz web</span><code class="shortcut">{fqdn}</code>'
                kind = '<span class="access-type access-type-dns">Servicio de red</span>'
                endpoint = escape(item["ip"])
            initials = "".join(word if word.isupper() else word[0] for word in item["name"].split())[:3]
            cards.append(f'''<article class="service-card" data-search="{search}">
  <div class="card-top"><span class="service-symbol" aria-hidden="true">{escape(initials)}</span>{kind}</div>
  <h3>{escape(item['name'])}</h3>
  <p>{escape(item['description'])}</p>
  <div class="endpoint">{endpoint}</div>
  <div class="card-actions">{action}</div>
</article>''')
        width = ' data-group-size="wide"' if len(items) > 1 else ''
        sections.append(f'''<section class="group" aria-labelledby="group-{number}"{width}>
  <div class="group-heading"><h2 id="group-{number}">{escape(category)}</h2><span class="group-count">{len(items)}</span></div>
  <div class="grid">{''.join(cards)}</div>
</section>''')
    template = (Path(__file__).parent / "templates" / "landing.html").read_text(encoding="utf-8")
    logo = Path(__file__).resolve().parents[2] / "logo.svg"
    logo_uri = "data:image/svg+xml;base64," + base64.b64encode(logo.read_bytes()).decode("ascii")
    favicon = Path(__file__).parent / "panel" / "public" / "favicon.svg"
    favicon_uri = "data:image/svg+xml;base64," + base64.b64encode(favicon.read_bytes()).decode("ascii")
    context = {"__DOMAIN__": escape(cfg["BASE_DOMAIN"]),
               "__LANDING_HOST__": escape(f"{cfg['LANDING_SUBDOMAIN']}.{cfg['BASE_DOMAIN']}"),
               "__COUNT__": str(len(services)), "__CATEGORY_COUNT__": str(len(groups)),
               "__LOGO_DATA__": logo_uri, "__FAVICON_DATA__": favicon_uri}
    # Una sola sustitución por marcador evita interpretar texto procedente del inventario.
    before, marker, after = template.partition("<!-- SERVICES -->")
    if not marker:
        raise ConfigError("La plantilla de la landing necesita el marcador SERVICES")
    for key, value in context.items():
        before, after = before.replace(key, value), after.replace(key, value)
    return before + "\n".join(sections) + after


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
