#!/usr/bin/env python3
"""Bounded TCP probe and NDI discovery with the Finder and SDK listeners.

ABI checked against the official NDI SDK v6 Linux Find.h, SendListener.h, and RecvListener.h.
NDI_CONFIG_DIR is set before loading/initializing the SDK, in this isolated process.
"""
import ctypes
import ctypes.util
from collections import Counter
import json
import os
from pathlib import Path
import re
import socket
import sys
import time


class FindCreate(ctypes.Structure):
    _fields_ = [('show_local_sources', ctypes.c_bool), ('p_groups', ctypes.c_char_p), ('p_extra_ips', ctypes.c_char_p)]


class Source(ctypes.Structure):
    # p_url_address and deprecated p_ip_address are the same pointer union.
    _fields_ = [('p_ndi_name', ctypes.c_char_p), ('p_url_address', ctypes.c_char_p)]


class ListenerCreate(ctypes.Structure):
    _fields_ = [('p_url_address', ctypes.c_char_p)]


class Sender(ctypes.Structure):
    # Standard SDK 6.3+ Processing.NDI.SendListener.h.
    _fields_ = [('p_uuid', ctypes.c_char_p), ('p_name', ctypes.c_char_p), ('p_metadata', ctypes.c_char_p),
                ('p_address', ctypes.c_char_p), ('port', ctypes.c_int),
                ('p_groups', ctypes.POINTER(ctypes.c_char_p)), ('num_groups', ctypes.c_uint32),
                ('events_subscribed', ctypes.c_bool)]


class Receiver(ctypes.Structure):
    # Standard SDK 6.2+ Processing.NDI.RecvListener.h (no Advanced event APIs).
    _fields_ = [('p_uuid',ctypes.c_char_p), ('p_name',ctypes.c_char_p),
        ('p_input_uuid',ctypes.c_char_p), ('p_input_name',ctypes.c_char_p), ('p_address',ctypes.c_char_p),
        ('p_streams',ctypes.POINTER(ctypes.c_int)), ('num_streams',ctypes.c_uint32),
        ('p_commands',ctypes.POINTER(ctypes.c_int)), ('num_commands',ctypes.c_uint32), ('events_subscribed',ctypes.c_bool)]


class LibraryUnavailable(Exception):
    pass


class RuntimeMissing(LibraryUnavailable):
    pass


def load_library():
    override = os.environ.get('NDI_LIBRARY_PATH')
    candidates = [override] if override else ['/opt/ndi-runtime/lib/libndi.so.6', ctypes.util.find_library('ndi'),
                  '/opt/ndi-runtime/lib/libndi.so.5', 'libndi.so.6', 'libndi.so.5', 'libndi.so']
    for candidate in dict.fromkeys(filter(None, candidates)):
        try:
            return ctypes.CDLL(candidate)
        except OSError:
            pass
    raise RuntimeMissing('Falta libndi para consultar fuentes y receptores en el CT del panel.')


def source_details(name, address):
    match = re.fullmatch(r'(.*?) \((.*)\)', name, re.DOTALL)
    return {'full_name': name, 'host': match[1] if match else name, 'stream': match[2] if match else '', 'url': address}


def decode(value):
    return (value or b'').decode('utf-8', errors='replace')


def bind_listener(lib, direction, collection, record, descriptor):
    prefix = f'NDIlib_{direction}_listener_'
    signatures = {'create': (ctypes.c_void_p, [ctypes.POINTER(ListenerCreate)]),
                  'destroy': (None, [ctypes.c_void_p]), 'is_connected': (ctypes.c_bool, [ctypes.c_void_p]),
                  f'wait_for_{collection}': (ctypes.c_bool, [ctypes.c_void_p, ctypes.c_uint32]),
                  f'get_{collection}': (ctypes.POINTER(record), [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)])}
    try:
        functions = {name: getattr(lib, prefix + name) for name in signatures}
    except AttributeError:
        return {}, None
    for name, (result, arguments) in signatures.items():
        functions[name].restype, functions[name].argtypes = result, arguments
    return functions, functions['create'](ctypes.byref(descriptor))


def add_advertised_senders(sources, items, count, groups):
    expected_groups = {group.strip() for group in groups.split(',') if group.strip()} or {'Public'}
    finder_endpoints = {(item['full_name'].casefold(), item['url']): item for item in sources}
    advertised_ids = set()
    for index in range(count):
        item = items[index]
        identity = decode(item.p_uuid)
        if identity and identity in advertised_ids:
            continue
        advertised_ids.add(identity)
        item_groups = {decode(item.p_groups[i]) for i in range(min(item.num_groups, 256))} if item.p_groups else set()
        if expected_groups.isdisjoint(item_groups or {'Public'}):
            continue
        address = decode(item.p_address)
        if address and 0 < item.port <= 65535:
            if ':' in address and not address.startswith('['):
                address = f'[{address}]'
            address = f'{address}:{item.port}'
        name = decode(item.p_name)
        if not name:
            continue
        source = finder_endpoints.pop((name.casefold(), address), None)
        if source is None:
            source = source_details(name, address)
            sources.append(source)
        if identity:
            source['id'] = identity


def enumerate_sources(settings, deadline, loader=load_library, with_receivers=False):
    os.environ['NDI_CONFIG_DIR'] = settings['config_dir']
    if settings.get('resolved_ip'):
        # Give the SDK the same numeric endpoint that passed TCP, even for DNS hosts.
        path = Path(settings['config_dir'])/'ndi-config.v1.json'
        config = json.loads(path.read_text(encoding='utf-8'))
        address = settings['resolved_ip']
        config['ndi']['networks']['discovery'] = f'[{address}]:{settings["port"]}' if ':' in address else f'{address}:{settings["port"]}'
        path.write_text(json.dumps(config),encoding='utf-8')
    lib = loader()
    signatures = {
        'NDIlib_initialize': (ctypes.c_bool, []), 'NDIlib_destroy': (None, []),
        'NDIlib_find_create_v2': (ctypes.c_void_p, [ctypes.POINTER(FindCreate)]),
        'NDIlib_find_destroy': (None, [ctypes.c_void_p]),
        'NDIlib_find_wait_for_sources': (ctypes.c_bool, [ctypes.c_void_p, ctypes.c_uint32]),
        'NDIlib_find_get_current_sources': (ctypes.POINTER(Source), [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)])}
    for name, (result, arguments) in signatures.items():
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = result, arguments
    if not lib.NDIlib_initialize():
        raise LibraryUnavailable('libndi no pudo inicializarse; revisa compatibilidad de CPU y dependencias del runtime.')
    finder = None
    sender_listener = receiver_listener = None
    receiver_supported = False
    receiver_warning = 'El runtime no permite consultar receptores anunciados; se necesita NDI 6.2 o posterior.'
    try:
        groups = settings['groups'].encode('utf-8') if settings['groups'] else None
        # p_extra_ips is NOT the Discovery Server: it queries source hosts on 5960.
        finder = lib.NDIlib_find_create_v2(ctypes.byref(FindCreate(True, groups, None)))
        if not finder:
            raise LibraryUnavailable('libndi no pudo crear el finder. Revisa el runtime y su configuración.')
        waits = [(lib.NDIlib_find_wait_for_sources, lib.NDIlib_find_get_current_sources, finder)]
        address = settings.get('resolved_ip', settings.get('ip', ''))
        endpoint = (f'[{address}]' if ':' in address else address) + ':' + str(settings['port']) if address else None
        descriptor = ListenerCreate(endpoint.encode('utf-8') if endpoint else None)
        tx, sender_listener = bind_listener(lib, 'send', 'senders', Sender, descriptor)
        if sender_listener:
            waits.append((tx['wait_for_senders'], tx['get_senders'], sender_listener))
        if with_receivers:
            rx, receiver_listener = bind_listener(lib, 'recv', 'receivers', Receiver, descriptor)
            receiver_supported = bool(rx)
            if receiver_supported:
                receiver_warning = 'No se ha podido consultar los receptores. Revisa que Discovery Server admita NDI 6.2 o posterior.'
            if receiver_listener:
                waits.append((rx['wait_for_receivers'], rx['get_receivers'], receiver_listener))
        # Accumulate discovery updates for the entire budget; the first event is often partial.
        while time.monotonic() < deadline:
            for wait, current, handle in waits:
                remaining = int((deadline - time.monotonic()) * 1000)
                if remaining <= 0:
                    break
                if wait(handle, min(100, remaining)):
                    current(handle, ctypes.byref(ctypes.c_uint32()))
            if remaining <= 0:
                break
        count = ctypes.c_uint32()
        sources = lib.NDIlib_find_get_current_sources(finder, ctypes.byref(count))
        if count.value > 10000:
            raise LibraryUnavailable('La respuesta NDI supera el máximo de 10.000 fuentes.')
        # Copy borrowed SDK memory before destroying the finder; preserve opaque URL values.
        result = [source_details(decode(sources[i].p_ndi_name), decode(sources[i].p_url_address)) for i in range(count.value)] if sources else []
        if sender_listener and tx['is_connected'](sender_listener):
            count = ctypes.c_uint32()
            advertised = tx['get_senders'](sender_listener, ctypes.byref(count))
            if advertised and count.value <= 10000:
                add_advertised_senders(result, advertised, count.value, settings['groups'])
        names = Counter(item['full_name'] for item in result)
        for item in result:
            if names[item['full_name']] > 1 and not item.get('id'):
                item['id'] = f'{item["full_name"]}\0{item["url"]}'
        if not with_receivers:
            return result
        receivers = []
        receiver_observed = bool(receiver_listener and rx['is_connected'](receiver_listener))
        if receiver_observed:
            receiver_count = ctypes.c_uint32()
            advertised = rx['get_receivers'](receiver_listener,ctypes.byref(receiver_count))
            if receiver_count.value > 10000:
                receiver_observed = False
                receiver_warning = 'La respuesta supera el máximo de 10.000 receptores.'
            elif advertised:
                for index in range(receiver_count.value):
                    item = advertised[index]
                    receiver = source_details(decode(item.p_name),decode(item.p_address))
                    receiver.update(id=decode(item.p_uuid),stream=decode(item.p_input_name) or receiver['stream'])
                    receivers.append(receiver)
        return {'sources':result,'sources_count':len(result),'sources_observed':True,
            'receivers':receivers,'receivers_count':len(receivers),'receivers_supported':receiver_supported,
            'receivers_observed':receiver_observed,'receivers_warning':None if receiver_observed else receiver_warning}
    finally:
        if receiver_listener:
            rx['destroy'](receiver_listener)
        if sender_listener:
            tx['destroy'](sender_listener)
        if finder:
            lib.NDIlib_find_destroy(finder)
        lib.NDIlib_destroy()


def probe(settings):
    started = time.monotonic()
    result = {'online': False, 'latency_ms': None, 'warning': None, 'sources_count': 0, 'sources': [],
        'sources_observed':False,'receivers':[],'receivers_count':0,'receivers_supported':False,'receivers_observed':False,'receivers_warning':None}
    try:
        with socket.create_connection((settings['ip'], settings['port']), timeout=settings['timeout']) as connection:
            result.update(online=True, latency_ms=round((time.monotonic()-started)*1000, 2))
            settings = dict(settings,resolved_ip=connection.getpeername()[0])
    except OSError:
        result['warning'] = 'Servidor sin respuesta: no se pudo establecer la conexión TCP dentro del timeout.'
        return result
    try:
        result.update(enumerate_sources(settings, started + settings['timeout'],with_receivers=True))
    except (LibraryUnavailable, AttributeError, OSError) as error:
        result['runtime_missing'] = isinstance(error, RuntimeMissing)
        result['warning'] = str(error) if isinstance(error, LibraryUnavailable) else 'libndi no es compatible o le faltan dependencias. Revisa la instalación del runtime en el README.'
    return result


if __name__ == '__main__':
    # Native SDK diagnostics must never contaminate the JSON protocol on stdout.
    protocol_fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    settings = json.load(sys.stdin)
    result = probe(settings)
    with os.fdopen(protocol_fd, 'w', encoding='utf-8') as protocol:
        json.dump(result, protocol, ensure_ascii=True)
        protocol.write('\n')
