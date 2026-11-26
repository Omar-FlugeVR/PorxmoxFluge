"""Metadata for the panel version published from this repository."""

import argparse
import json
from pathlib import Path
import re


VERSION_PATTERN = r'[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}'
PANEL_TAG = re.compile(rf'panel-v({VERSION_PATTERN})')


def version_parts(version):
    if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
        raise ValueError('La versión del panel debe tener el formato 0.0.1.')
    return tuple(map(int, version.split('.')))


def validate_release(data):
    if not isinstance(data, dict):
        raise ValueError('panel-version.json debe contener un objeto JSON.')
    version = data.get('version')
    build_type = data.get('type')
    required = data.get('update_required')
    parts = version_parts(version)
    if not isinstance(build_type, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,24}', build_type):
        raise ValueError('El tipo de versión del panel no es válido.')
    if type(required) is not bool:
        raise ValueError('update_required debe ser true o false.')
    release = {'version': version, 'type': build_type, 'update_required': required}
    minimum = data.get('minimum_version')
    if minimum is not None:
        if version_parts(minimum) >= parts:
            raise ValueError('La versión mínima debe ser anterior a la que se publica.')
        release['minimum_version'] = minimum
    return release


def bump_release(data, increment, required, minimum=''):
    release = validate_release(data)
    parts = [int(part) for part in release['version'].split('.')]
    index = {'mayor': 0, 'menor': 1, 'parche': 2}[increment]
    parts[index] += 1
    parts[index + 1:] = [0] * (2 - index)
    release.update(version='.'.join(map(str, parts)), update_required=required)
    if minimum == 'ninguna':
        release.pop('minimum_version', None)
    elif minimum:
        release['minimum_version'] = minimum
    return validate_release(release)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Preparar panel-version.json para la publicación.')
    parser.add_argument('increment', choices=('parche', 'menor', 'mayor'))
    parser.add_argument('required', choices=('true', 'false'))
    parser.add_argument('minimum', nargs='?', default='')
    args = parser.parse_args()
    path = Path('panel-version.json')
    release = bump_release(json.loads(path.read_text(encoding='utf-8')), args.increment, args.required == 'true', args.minimum.strip())
    path.write_text(json.dumps(release, indent=2) + '\n', encoding='utf-8')
    print(release['version'])
