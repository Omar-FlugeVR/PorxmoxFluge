"""Metadata for the panel version published from this repository."""

import re


def validate_release(data):
    if not isinstance(data, dict):
        raise ValueError('panel-version.json debe contener un objeto JSON.')
    version = data.get('version')
    build_type = data.get('type')
    required = data.get('update_required')
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}', version):
        raise ValueError('La versión del panel debe tener el formato 0.0.1.')
    if not isinstance(build_type, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,24}', build_type):
        raise ValueError('El tipo de versión del panel no es válido.')
    if type(required) is not bool:
        raise ValueError('update_required debe ser true o false.')
    return {'version': version, 'type': build_type, 'update_required': required}
