"""Native probe checks use loopback sockets and a fake SDK ABI; no NDI runtime/LAN."""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PHP = os.environ.get('FLUGE_TEST_PHP') or shutil.which('php')
if not PHP and os.name == 'nt':
    candidate = Path(tempfile.gettempdir())/'fluge-dev-tools/php/php.exe'
    PHP = str(candidate) if candidate.exists() else None
spec = importlib.util.spec_from_file_location('ndi_probe',ROOT/'cts/caddy/panel/bin/ndi-probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Function:
    def __init__(self, operation): self.operation = operation
    def __call__(self,*args): return self.operation(*args)


class NdiProbeTests(unittest.TestCase):
    @unittest.skipUnless(PHP,'PHP required for native process integration')
    def test_php_launches_native_helper_and_cleans_temporary_configuration(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as listener:
            listener.bind(('127.0.0.1',0)); listener.listen(1)
            settings={'ip':'127.0.0.1','port':listener.getsockname()[1],'timeout':1,'groups':'Public'}
            code="require getenv('FLUGE_NDI_AUTOLOAD'); $s=new Homelab\\JsonStore(getenv('FLUGE_STATE_DIR')); $m=new Homelab\\NdiMonitor($s,new Homelab\\ConfigRepository($s)); echo json_encode($m->test(json_decode(stream_get_contents(STDIN),true)));"
            result=subprocess.run([PHP,'-r',code],input=json.dumps(settings),text=True,capture_output=True,timeout=8,
                env=dict(os.environ,FLUGE_DEMO='0',NDI_MOCK='false',FLUGE_PYTHON=sys.executable,
                    FLUGE_NDI_AUTOLOAD=str(ROOT/'cts/caddy/panel/src/autoload.php'),FLUGE_STATE_DIR=directory,
                    NDI_LIBRARY_PATH=str(Path(directory)/'not-installed.so')))
            self.assertEqual(result.returncode,0,result.stderr)
            response=json.loads(result.stdout)
            self.assertTrue(response['online'],response)
            self.assertIn('libndi',response['warning'])
            self.assertTrue(response['runtime_missing'])
            self.assertEqual(response['sources_count'],0)
            self.assertEqual(list(Path(directory).iterdir()),[],'No SDK settings or processes may survive a one-shot request')

    def test_missing_runtime_preserves_successful_tcp_diagnosis(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0)); listener.listen(1)
            settings={'ip':'127.0.0.1','port':listener.getsockname()[1],'timeout':1,'groups':'','config_dir':''}
            with patch.object(probe,'enumerate_sources',side_effect=probe.RuntimeMissing('Falta libndi.')):
                result=probe.probe(settings)
        self.assertTrue(result['online'])
        self.assertGreaterEqual(result['latency_ms'],0)
        self.assertIn('libndi',result['warning'])
        self.assertTrue(result['runtime_missing'])
        self.assertEqual(result['sources_count'],0)

    def test_offline_server_does_not_load_sdk(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))  # Reserved port with no listening server.
            settings={'ip':'127.0.0.1','port':listener.getsockname()[1],'timeout':1}
            with patch.object(probe,'enumerate_sources') as find:
                result=probe.probe(settings)
                find.assert_not_called()
        self.assertFalse(result['online'])
        self.assertIsNone(result['latency_ms'])
        self.assertIn('TCP',result['warning'])

    def test_library_lookup_has_clean_fallback(self):
        with patch.object(probe.ctypes.util,'find_library',return_value=None), patch.object(probe.ctypes,'CDLL',side_effect=OSError('not installed')):
            with self.assertRaisesRegex(probe.LibraryUnavailable,'Falta libndi'):
                probe.load_library()

    def test_sdk_abi_configuration_and_borrowed_source_memory(self):
        calls=[]
        sources=(probe.Source*2)(probe.Source(b'CAM-01 (Main (Clean))',b'192.168.10.51:5961'),
                                  probe.Source(b'VMIX-MAIN (Program Out)',b'ndi://opaque-address/stream?transport=tcp'))
        def create(pointer):
            descriptor=ctypes.cast(pointer,ctypes.POINTER(probe.FindCreate)).contents
            self.assertTrue(descriptor.show_local_sources)
            self.assertEqual(descriptor.p_groups,b'Public,camaras')
            self.assertIsNone(descriptor.p_extra_ips,'Discovery endpoint must not be mistaken for source IPs on 5960')
            calls.append('create'); return 123
        def current(instance,count):
            self.assertEqual(instance,123)
            ctypes.cast(count,ctypes.POINTER(ctypes.c_uint32))[0]=2
            calls.append('sources'); return sources
        def destroy(instance):
            sources[0].p_ndi_name=b'gone'; sources[0].p_url_address=None
            calls.append('destroy finder')
        class Library: pass
        library=Library()
        operations={'NDIlib_initialize':lambda:True,'NDIlib_destroy':lambda:calls.append('destroy SDK'),
            'NDIlib_find_create_v2':create,'NDIlib_find_get_current_sources':current,
            'NDIlib_find_wait_for_sources':lambda *_:False,'NDIlib_find_destroy':destroy}
        for name,operation in operations.items(): setattr(library,name,Function(operation))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{},clear=False):
            path=Path(directory)/'ndi-config.v1.json'
            path.write_text(json.dumps({'ndi':{'networks':{'discovery':'ndi.local:5969'},'groups':{'recv':'Public,camaras'}}}))
            settings={'groups':'Public,camaras','config_dir':directory,'resolved_ip':'192.168.10.20','port':5969}
            def loader():
                self.assertEqual(os.environ['NDI_CONFIG_DIR'],directory)
                self.assertEqual(json.loads(path.read_text())['ndi']['networks']['discovery'],'192.168.10.20:5969')
                return library
            result=probe.enumerate_sources(settings,time.monotonic()-1,loader=loader)
        self.assertEqual(result[0],{'full_name':'CAM-01 (Main (Clean))','host':'CAM-01','stream':'Main (Clean)','url':'192.168.10.51:5961'})
        self.assertEqual(result[1]['url'],'ndi://opaque-address/stream?transport=tcp')
        self.assertEqual(calls,['create','sources','destroy finder','destroy SDK'])
        self.assertEqual(library.NDIlib_find_create_v2.restype,ctypes.c_void_p)

    def test_source_without_parentheses_or_address_remains_visible(self):
        self.assertEqual(probe.source_details('Remote camera',''),{'full_name':'Remote camera','host':'Remote camera','stream':'','url':''})

    def test_discovery_merges_advertised_senders_and_keeps_distinct_senders_and_receivers(self):
        calls=[]
        senders=(probe.Source*1)(probe.Source(b'CAM-01 (Main)',b'192.168.10.51:5961'))
        receivers=(probe.Receiver*2)()
        for index, receiver in enumerate(receivers, start=1):
            receiver.p_uuid = f'recv-{index}'.encode()
            receiver.p_name = b'VMIX-MAIN (Input)'
            receiver.p_input_name = b'Cameras'
            receiver.p_address = b'192.168.10.60'
        private_groups = (ctypes.c_char_p * 2)(b'privado', None)
        advertised = (probe.Sender * 3)()
        for index, port in enumerate([5961, 5962, 5963]):
            advertised[index].p_uuid = f'tx-{port}'.encode()
            advertised[index].p_name = b'CAM-01 (Main)'
            advertised[index].p_address = b'192.168.10.51'
            advertised[index].port = port
        advertised[2].p_groups = private_groups
        advertised[2].num_groups = 1
        def current(instance,count):
            items = senders if instance == 11 else receivers
            ctypes.cast(count,ctypes.POINTER(ctypes.c_uint32))[0] = len(items)
            return items
        def listener(pointer):
            descriptor=ctypes.cast(pointer,ctypes.POINTER(probe.ListenerCreate)).contents
            self.assertEqual(descriptor.p_url_address,b'ndi.local:5959')
            return 22
        def current_senders(instance, count):
            self.assertEqual(instance, 22)
            ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32))[0] = 3
            return advertised
        def destroy_sender(instance):
            self.assertEqual(instance, 22)
            advertised[1].p_name = b'gone'
            calls.append('sender destroyed')
        def destroy_receiver(instance):
            self.assertEqual(instance,22);receivers[0].p_name=b'gone';calls.append('receiver destroyed')
        class Library: pass
        library=Library()
        operations={'NDIlib_initialize':lambda:True,'NDIlib_destroy':lambda:calls.append('SDK destroyed'),
            'NDIlib_find_create_v2':lambda *_:11,'NDIlib_find_destroy':lambda *_:calls.append('finder destroyed'),
            'NDIlib_find_wait_for_sources':lambda *_:False,'NDIlib_find_get_current_sources':current,
            'NDIlib_send_listener_create':listener,'NDIlib_send_listener_destroy':destroy_sender,
            'NDIlib_send_listener_is_connected':lambda instance:True,'NDIlib_send_listener_wait_for_senders':lambda *_:False,
            'NDIlib_send_listener_get_senders':current_senders,
            'NDIlib_recv_listener_create':listener,'NDIlib_recv_listener_destroy':destroy_receiver,
            'NDIlib_recv_listener_is_connected':lambda instance:True,'NDIlib_recv_listener_wait_for_receivers':lambda *_:False,
            'NDIlib_recv_listener_get_receivers':current}
        for name,operation in operations.items():setattr(library,name,Function(operation))
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{},clear=False):
            result=probe.enumerate_sources({'ip':'ndi.local','port':5959,'groups':'','config_dir':directory},time.monotonic()-1,loader=lambda:library,with_receivers=True)
        self.assertTrue(result['receivers_observed'])
        self.assertTrue(result['sources_observed'])
        self.assertEqual(result['sources_count'], 2)
        self.assertEqual([(source['full_name'], source['url'], source['id']) for source in result['sources']],
                         [('CAM-01 (Main)', '192.168.10.51:5961', 'tx-5961'), ('CAM-01 (Main)', '192.168.10.51:5962', 'tx-5962')])
        self.assertIsNone(result['receivers_warning'])
        self.assertEqual(result['receivers_count'], 2)
        self.assertEqual([receiver['id'] for receiver in result['receivers']], ['recv-1', 'recv-2'])
        self.assertEqual(result['receivers'][0],{'id':'recv-1','full_name':'VMIX-MAIN (Input)','host':'VMIX-MAIN','stream':'Cameras','url':'192.168.10.60'})
        self.assertEqual(calls,['receiver destroyed','sender destroyed','finder destroyed','SDK destroyed'])

    @unittest.skipUnless(PHP, 'PHP required for NDI history testing')
    def test_history_keeps_both_senders_with_same_name_and_preserves_earlier_observation(self):
        code = r'''
require getenv('FLUGE_NDI_AUTOLOAD');
$store = new Homelab\JsonStore(getenv('FLUGE_STATE_DIR'));
$history = new Homelab\NdiHistory($store);
$settings = ['ip' => 'test.local', 'port' => 5959, 'groups' => ''];
$first = ['full_name' => 'CAM (Main)', 'host' => 'CAM', 'stream' => 'Main', 'url' => '192.168.10.51:5961'];
$status = ['mock' => false, 'online' => true, 'sources_observed' => true, 'last_updated' => '2026-11-26T18:00:00Z', 'sources' => [$first]];
$history->record($settings, $status);
$status['last_updated'] = '2026-11-26T18:01:00Z';
$status['sources'] = [$first + ['id' => 'tx-one'], array_replace($first, ['id' => 'tx-two', 'url' => '192.168.10.51:5962'])];
echo json_encode($history->record($settings, $status)['entities']);
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([PHP, '-r', code], text=True, capture_output=True, check=True, timeout=10,
                                    env=dict(os.environ, FLUGE_STATE_DIR=directory,
                                             FLUGE_NDI_AUTOLOAD=str(ROOT/'cts/caddy/panel/src/autoload.php')))
        by_url = {source['url']: source for source in json.loads(result.stdout)}
        self.assertEqual(len(by_url), 2)
        self.assertTrue(all(source['available'] for source in by_url.values()))
        self.assertEqual(by_url['192.168.10.51:5961']['first_seen'], '2026-11-26T18:00:00Z')
        self.assertEqual(by_url['192.168.10.51:5962']['first_seen'], '2026-11-26T18:01:00Z')

    def test_old_runtime_still_lists_senders_without_inventing_receiver_availability(self):
        class Library: pass
        library=Library()
        def current(_,count):
            ctypes.cast(count,ctypes.POINTER(ctypes.c_uint32))[0]=0
            return None
        operations={'NDIlib_initialize':lambda:True,'NDIlib_destroy':lambda:None,'NDIlib_find_create_v2':lambda *_:1,
            'NDIlib_find_destroy':lambda *_:None,'NDIlib_find_wait_for_sources':lambda *_:False,'NDIlib_find_get_current_sources':current}
        for name,operation in operations.items():setattr(library,name,Function(operation))
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{},clear=False):
            result=probe.enumerate_sources({'ip':'ndi.local','port':5959,'groups':'','config_dir':directory},time.monotonic()-1,loader=lambda:library,with_receivers=True)
        self.assertTrue(result['sources_observed'])
        self.assertFalse(result['receivers_observed'])
        self.assertFalse(result['receivers_supported'])
        self.assertEqual(result['receivers'],[])
        self.assertIn('6.2',result['receivers_warning'])


if __name__ == '__main__': unittest.main()
