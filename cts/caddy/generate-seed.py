#!/usr/bin/env python3
"""Convierte el inventario del instalador en el estado inicial del panel."""

def render(cfg, services):
    category = 'Infraestructura'
    name = 'Servicio de control'
    address = cfg['CADDY_IP']
    categories = list(dict.fromkeys([item['category'] for item in services] + [category]))
    entries = [
        {'id': 'seed-' + item['subdomain'], 'name': item['name'],
         'url': item['target'], 'ip': item.get('ip', ''), 'domain': item['fqdn'],
         'proxyMode': item['mode'], 'category': item['category'],
         'description': item['description'], 'sshEnabled': False}
        for item in services]
    # The panel already serves its own dashboard domain. Its inventory card opens it directly.
    entries.append({'id': 'panel-landing', 'name': name, 'url': f'http://{address}/',
                    'domain': '', 'proxyMode': 'redirect', 'category': category,
                    'description': 'Panel de acceso de la instalación.', 'sshEnabled': False})
    return {
        'services': entries,
        'ips': [{'id': 'panel-landing-device', 'hostname': name, 'ip': address, 'tag': category}],
        'categories': [{'id': f'cat-{index}', 'name': name, 'color': '#6db9b2'}
                       for index, name in enumerate(categories)],
    }
