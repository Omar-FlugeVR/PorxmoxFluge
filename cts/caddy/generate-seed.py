#!/usr/bin/env python3
"""Convierte el inventario del instalador en el estado inicial del panel."""

def render(cfg, services):
    categories = list(dict.fromkeys(item['category'] for item in services)) or ['Infraestructura']
    return {'services': [
        {'id': 'seed-' + item['subdomain'], 'name': item['name'],
         'url': item['target'], 'ip': item.get('ip', ''), 'domain': item['fqdn'],
         'proxyMode': item['mode'], 'category': item['category'],
         'description': item['description'], 'sshEnabled': False}
        for item in services], 'ips': [],
        'categories': [{'id': f'cat-{index}', 'name': name, 'color': '#6db9b2'}
                       for index, name in enumerate(categories)]}
