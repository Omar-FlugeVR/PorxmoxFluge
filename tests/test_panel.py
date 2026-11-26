"""HTTP regressions of the actual panel, with isolated state and no LAN traffic."""
import copy
from hashlib import sha256
import http.cookiejar
import importlib.util
import json
import os
import secrets
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PHP = os.environ.get('FLUGE_TEST_PHP') or shutil.which('php')
if not PHP and os.name == 'nt':
    candidate = Path(tempfile.gettempdir()) / 'fluge-dev-tools/php/php.exe'
    PHP = str(candidate) if candidate.exists() else None


@unittest.skipUnless(PHP, 'PHP 8.2+ required for panel HTTP tests')
class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.state = Path(cls.tmp.name)
        spec = importlib.util.spec_from_file_location('preview_panel', ROOT / 'scripts/preview-panel.py')
        cls.preview = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.preview)
        cls.preview.initialize(PHP, cls.state)
        cls.initial = {p.name: p.read_bytes() for p in cls.state.glob('*.json')}
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            cls.port = listener.getsockname()[1]
        cls.base = f'http://127.0.0.1:{cls.port}'
        cls.log = (cls.state / 'server.log').open('wb')
        cls.server = subprocess.Popen([PHP, '-d', f'session.save_path={cls.state / "sessions"}', '-S', f'127.0.0.1:{cls.port}', '-t', str(ROOT / 'cts/caddy/panel/public'), str(ROOT / 'cts/caddy/panel/bin/demo-router.php')],
            env=dict(os.environ, FLUGE_DEMO='1', FLUGE_STATE_DIR=str(cls.state)), stdout=cls.log, stderr=cls.log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.base + '/', timeout=.3).close()
                return
            except OSError:
                time.sleep(.05)
        raise RuntimeError('PHP did not start')

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=10)
        cls.log.close()
        cls.tmp.cleanup()

    def setUp(self):
        for p in self.state.glob('*.json'):
            if p.name not in self.initial:
                p.unlink()
        for name, data in self.initial.items():
            (self.state / name).write_bytes(data)
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = None
        self.login()

    def request(self, action, data=None, client=None, csrf=True):
        payload = None if data is None else json.dumps({'action': action, **data}).encode()
        headers = {'Content-Type': 'application/json'}
        token = csrf if isinstance(csrf, str) else self.csrf if csrf else None
        if token:
            headers['X-CSRF-Token'] = token
        path = action if action.startswith('/') else '/api.php' + ('?action=' + action if data is None else '')
        request = urllib.request.Request(self.base + path, data=payload, headers=headers)
        try:
            with (client or self.client).open(request, timeout=8) as response:
                self.last_headers = response.headers
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            self.last_headers = error.headers
            return error.code, json.load(error)

    def login(self, username='admin', password='FlugeDemo2026'):
        status, session = self.request('session')
        self.csrf = session['csrfToken']
        status, session = self.request('login', {'username': username, 'password': password})
        self.assertEqual(status, 200, session)
        self.csrf = session['csrfToken']

    def inventory(self):
        return self.request('data')[1]

    def another_session(self, username, password):
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        status, session = self.request('login', {'username':username,'password':password}, client=client, csrf=False)
        self.assertEqual(status, 200, session)
        return client, session

    def test_login_lockouts_escalate_and_reset_after_success(self):
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        wrong = {'username': 'admin', 'password': 'incorrecta'}
        correct = {'username': 'admin', 'password': 'FlugeDemo2026'}
        attempts = self.state / 'login-attempts.json'
        address = sha256(b'127.0.0.1').hexdigest()
        for delay in (60, 60, 60, 300, 300, 300, 600, 600):
            with self.subTest(delay=delay):
                for _ in range(4):
                    self.assertEqual(self.request('login', wrong, client=client)[0], 401)
                status, locked = self.request('login', wrong, client=client)
                self.assertEqual((status, locked['retry_after']), (429, delay))
                self.assertEqual(self.last_headers['Retry-After'], str(delay))
                saved = attempts.read_bytes()
                status, session = self.request('session', client=client)
                self.assertEqual(status, 200)
                self.assertGreater(session['login_retry_after'], 0)
                self.assertLessEqual(session['login_retry_after'], delay)
                status, locked = self.request('login', correct, client=client)
                self.assertEqual(status, 429)
                self.assertEqual(self.last_headers['Retry-After'], str(locked['retry_after']))
                self.assertEqual(attempts.read_bytes(), saved)
                state = json.loads(saved)
                state[address]['blockedUntil'] = int(time.time()) - 1
                attempts.write_text(json.dumps(state), encoding='utf-8')
                self.assertEqual(self.request('session', client=client)[1]['login_retry_after'], 0)
        for _ in range(4):
            self.assertEqual(self.request('login', wrong, client=client)[0], 401)
        status, session = self.request('login', correct, client=client)
        self.assertEqual(status, 200)
        self.assertEqual(self.request('logout', {}, client=client, csrf=session['csrfToken'])[0], 200)
        for _ in range(4):
            self.assertEqual(self.request('login', wrong, client=client)[0], 401)
        status, locked = self.request('login', wrong, client=client)
        self.assertEqual((status, locked['retry_after']), (429, 60))

    def test_login_throttle_reads_the_previous_state_format(self):
        attempts = self.state / 'login-attempts.json'
        address = sha256(b'127.0.0.1').hexdigest()
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        state = {address: {'start': int(time.time()) - 600, 'count': 5}}
        attempts.write_text(json.dumps(state), encoding='utf-8')
        status, session = self.request('session', client=client)
        self.assertEqual(status, 200)
        self.assertGreater(session['login_retry_after'], 0)
        self.assertLessEqual(session['login_retry_after'], 300)
        state[address] = {'start': int(time.time()), 'count': 4}
        attempts.write_text(json.dumps(state), encoding='utf-8')
        status, locked = self.request('login', {'username': 'admin', 'password': 'incorrecta'}, client=client)
        self.assertEqual((status, locked['retry_after']), (429, 60))

    def test_save_automatically_publishes_add_edit_delete_and_preserves_external_dns(self):
        (self.state / 'demo-dns.json').write_text(json.dumps([{'domain':'manual.lan','answer':'192.168.1.99'}]))
        data = self.inventory()
        data['categories'].append({'id':'cat-av','name':'AV','color':'#6db9b2'})
        data['services'] += [dict(id='mixer',name='Mixer',url='http://192.168.1.30:8080',domain='mixer.lan',proxyMode='redirect',category='AV',description='Control'),
                             dict(id='audio',name='Audio',url='',ip='192.168.1.31',domain='audio.lan',proxyMode='dns_only',category='AV',description='Audio')]
        status, saved = self.request('save_data', data)
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved['status'], 'ok')
        self.assertTrue(saved['dns_sync']['ok'])
        self.assertEqual(saved['dns_sync']['revision'], saved['revision'])
        result = self.request('applied')[1]
        self.assertIn({'domain':'mixer.lan','answer':'192.168.1.12'}, result['dns'])
        self.assertIn({'domain':'audio.lan','answer':'192.168.1.31'}, result['dns'])
        self.assertIn('redir "http://192.168.1.30:8080{uri}" 302', result['caddy'])
        self.assertNotIn('http://audio.lan', result['caddy'])
        self.assertNotIn('reverse_proxy', result['caddy'])
        data = self.inventory()
        next(s for s in data['services'] if s['id'] == 'mixer')['url'] = 'https://192.168.1.32:8443'
        status, edited = self.request('save_data', data)
        self.assertEqual(status, 200)
        self.assertTrue(edited['dns_sync']['ok'])
        self.assertIn('https://192.168.1.32:8443{uri}', self.request('applied')[1]['caddy'])
        data = self.inventory()
        data['services'] = [s for s in data['services'] if s['id'] not in ('mixer','audio')]
        status, saved = self.request('save_data', data)
        self.assertEqual(status, 200)
        self.assertTrue(saved['dns_sync']['ok'])
        result = self.request('applied')[1]
        self.assertEqual([e for e in result['dns'] if e['domain'] in ('mixer.lan','audio.lan')], [])
        self.assertIn({'domain':'manual.lan','answer':'192.168.1.99'}, result['dns'])

    def test_conflicts_and_invalid_routes_never_replace_inventory(self):
        original = self.inventory()
        variants = [('proxyMode','proxy'), ('domain','inicio.lan'), ('domain','foreign.example'),
                    ('url','http://192.168.1.12'), ('url','http://companion.lan'),
                    ('url','http://example.com/{env.SECRET}'), ('url','http://example.com/\nfile_server')]
        for key,value in variants:
            data = copy.deepcopy(original)
            data['services'][0][key] = value
            status, body = self.request('save_data', data)
            self.assertEqual(status, 422, (key,value,body))
            self.assertEqual(self.inventory(), original)
        status, saved = self.request('save_data', original)
        self.assertEqual(status, 200)
        self.assertEqual(self.request('save_data', original)[0], 409)
        self.assertEqual(self.request('sync_infrastructure', {'revision':original['revision']})[0], 409)

    def test_permissions_and_csrf_are_enforced(self):
        self.assertEqual(self.request('save_data', self.inventory(), csrf=False)[0], 403)
        status, body = self.request('create_user', {'revision':0,'username':'viewer','password':'ViewerDemo1','confirmPassword':'ViewerDemo1','role':'user','enabled':True})
        self.assertEqual(status, 200, body)
        self.request('logout', {})
        self.login('viewer','ViewerDemo1')
        self.assertEqual(self.request('data')[0], 200)
        self.assertEqual(self.request('updates')[0], 403)
        for action,data in [('save_data',self.inventory()),('scan_chunk',{'ips':['192.168.1.30']}),('sync_infrastructure',{'revision':1}),('save_category',{'name':'Forbidden','revision':1}),('delete_category',{'id':'cat-0','revision':1})]:
            self.assertEqual(self.request(action,data)[0], 403)

    def test_updates_returns_cached_report_to_administrators(self):
        self.assertEqual(self.request('updates'), (200, {'checkedAt': None, 'services': []}))
        report = {'checkedAt': '2026-11-26T12:00:00+00:00', 'services': {'caddy': {
            'installed': '2.11.8', 'latest': '2.11.9', 'status': 'update'}}}
        path = self.state / 'updates.json'
        path.write_text(json.dumps(report), encoding='utf-8')
        self.assertEqual(self.request('updates'), (200, report))
        self.assertEqual(self.request('updates', client=urllib.request.build_opener())[0], 401)

    def test_search_updates_requests_fresh_report_without_installing(self):
        path = self.state / 'updates.json'
        services = {'caddy': {'installed':'2.11.8', 'latest':'2.11.9', 'status':'update', 'canUpdate':True}}
        path.write_text(json.dumps({'checkedAt':None, 'services':services}), encoding='utf-8')
        self.assertEqual(self.request('check_updates', {}, csrf=False)[0], 403)
        viewer = {'revision':0, 'username':'checkviewer', 'password':'ViewerDemo1', 'confirmPassword':'ViewerDemo1', 'role':'user'}
        self.assertEqual(self.request('create_user', viewer)[0], 200)
        client, session = self.another_session('checkviewer', 'ViewerDemo1')
        self.assertEqual(self.request('check_updates', {}, client=client, csrf=session['csrfToken'])[0], 403)

        status, job = self.request('check_updates', {})
        self.assertEqual((status, job['role'], job['state']), (200, 'check', 'success'))
        report = self.request('updates')[1]
        self.assertTrue(report['checkedAt'])
        self.assertEqual(report['services'], services)
        self.assertEqual(report['job'], job)

        queued = {'id':'a' * 32, 'role':'check', 'created':int(time.time())}
        queue = self.state / 'update-request.json'
        queue.write_text(json.dumps(queued), encoding='utf-8')
        self.assertEqual(self.request('check_updates', {}), (200, {**queued, 'state':'queued'}))
        self.assertEqual(self.request('update_service', {'role':'caddy', 'version':'2.11.9'})[0], 409)
        queue.write_text(json.dumps({**queued, 'role':'caddy', 'version':'2.11.9'}), encoding='utf-8')
        self.assertEqual(self.request('check_updates', {})[0], 409)
        self.assertEqual(self.request('updates')[1]['checkedAt'], report['checkedAt'])

    def test_update_requests_use_the_report_and_require_acceptance_for_a_new_ndi_license(self):
        from datetime import datetime, timezone
        release = {'status':'update', 'canUpdate':True, 'installed':'SDK 6.3.1', 'latest':'SDK 6.4.0',
                   'license':{'hash':'a' * 64, 'text':'Example EULA'}}
        report = {'checkedAt':datetime.now(timezone.utc).isoformat(), 'services':{'ndi': release}}
        (self.state / 'updates.json').write_text(json.dumps(report), encoding='utf-8')
        request = {'role':'ndi', 'version':'SDK 6.4.0'}
        self.assertEqual(self.request('update_service', request, csrf=False)[0], 403)
        self.assertEqual(self.request('update_service', {**request, 'version':'SDK 6.3.0'})[0], 409)
        self.assertEqual(self.request('update_service', request)[0], 422)
        self.assertFalse((self.state / 'update-request.json').exists())
        status, job = self.request('update_service', {**request, 'accepted':True, 'licenseHash':'a' * 64})
        self.assertEqual((status, job['state']), (200, 'success'))
        results = self.request('updates')[1]
        self.assertEqual(results['services']['ndi']['installed'], 'SDK 6.4.0')
        self.assertEqual(results['job']['id'], job['id'])

    def test_panel_updates_require_an_announced_commit(self):
        from datetime import datetime, timezone
        target = 'b' * 40
        latest = {'version':'0.0.2','type':'test','update_required':False}
        entry = {'latest':target,'latestRelease':latest,'status':'current','canUpdate':False}
        report = {'checkedAt':datetime.now(timezone.utc).isoformat(),'services':{'panel':entry}}
        path = self.state / 'updates.json'
        path.write_text(json.dumps(report), encoding='utf-8')
        self.assertEqual(self.request('update_service', {'role':'panel','version':target})[0], 409)
        entry.update(status='update', canUpdate=True, tag='panel-v0.0.2')
        bridge = 'a' * 40
        entry['upgradePath'] = [{'version':'0.0.1', 'commit':bridge}, {'version':'0.0.2', 'commit':target}]
        path.write_text(json.dumps(report), encoding='utf-8')
        self.assertEqual(self.request('update_service', {'role':'panel','version':target})[0], 409)
        status, job = self.request('update_service', {'role':'panel','version':target,'upgradePath':[bridge,target]})
        self.assertEqual(status, 200)
        self.assertEqual(job['planHash'], sha256(f'{bridge},{target}'.encode()).hexdigest())
        deployed = self.request('updates')[1]['services']['panel']
        self.assertEqual(deployed['installedRelease'], latest)
        self.assertNotIn('upgradePath', deployed)

    def test_categories_propagate_names_and_reassign_without_deleting_equipment(self):
        data = self.inventory()
        original = data['categories'][0]
        data['ips'].append({'id':'host-category','ip':'192.168.1.90','hostname':'Category host','tag':original['name']})
        self.assertEqual(self.request('save_data',data)[0],200)
        routes = self.request('applied')[1]['caddy']
        status, created = self.request('save_category',{'revision':self.inventory()['revision'],'name':'Producción'})
        self.assertEqual(status,200,created)
        self.assertTrue(created['dns_sync']['ok'])
        destination = next(c for c in created['inventory']['categories'] if c['name']=='Producción')
        status, renamed = self.request('save_category',{'revision':created['inventory']['revision'],'id':original['id'],'name':'Infraestructura técnica'})
        self.assertEqual(status,200,renamed)
        current = self.inventory()
        self.assertTrue(all(s['category']!='Infraestructura' for s in current['services']))
        self.assertEqual(current['ips'][0]['tag'],'Infraestructura técnica')
        self.assertEqual(self.request('applied')[1]['caddy'],routes)
        self.assertEqual(self.request('delete_category',{'revision':current['revision'],'id':original['id']})[0],422)
        self.assertEqual(self.request('delete_category',{'revision':current['revision'],'id':original['id'],'replacementId':original['id']})[0],422)
        status, deleted = self.request('delete_category',{'revision':current['revision'],'id':original['id'],'replacementId':destination['id']})
        self.assertEqual(status,200,deleted)
        after = deleted['inventory']
        self.assertEqual(len(after['services']),len(current['services']))
        self.assertEqual(len(after['ips']),len(current['ips']))
        self.assertEqual(after['ips'][0]['tag'],'Producción')
        self.assertFalse(any(c['id']==original['id'] for c in after['categories']))
        self.assertFalse(any(s['category']=='Infraestructura técnica' for s in after['services']))
        self.assertTrue(deleted['dns_sync']['ok'])
        self.assertEqual(self.request('runtime')[1]['lastSync']['revision'],after['revision'])

    def test_categories_reject_duplicates_stale_changes_and_last_category_removal(self):
        current = self.inventory()
        for name in ('','  ','infraestructura'):
            self.assertEqual(self.request('save_category',{'revision':current['revision'],'name':name})[0],422)
        self.assertEqual(self.request('save_category',{'revision':current['revision'],'name':'No CSRF'},csrf=False)[0],403)
        status, created = self.request('save_category',{'revision':current['revision'],'name':'Vídeo'})
        self.assertEqual(status,200)
        self.assertEqual(self.request('save_category',{'revision':current['revision'],'name':'Stale'})[0],409)
        self.assertEqual(self.request('save_category',{'revision':created['inventory']['revision'],'name':'VÍDEO'})[0],422)
        empty = next(c for c in created['inventory']['categories'] if c['name']=='Vídeo')
        status, deleted = self.request('delete_category',{'revision':created['inventory']['revision'],'id':empty['id']})
        self.assertEqual(status,200,deleted)
        data = deleted['inventory']
        only = data['categories'][0]
        data['categories'] = [only]
        for service in data['services']: service['category'] = only['name']
        status, saved = self.request('save_data',data)
        self.assertEqual(status,200,saved)
        self.assertEqual(self.request('delete_category',{'revision':saved['revision'],'id':only['id']})[0],422)
        invalid = self.inventory()
        invalid['categories'] = []
        self.assertEqual(self.request('save_data',invalid)[0],422)
        invalid = self.inventory()
        invalid['services'][0]['category'] = 'Missing category'
        self.assertEqual(self.request('save_data',invalid)[0],422)

    def test_new_accounts_require_matching_password_confirmation(self):
        original = self.request('users')[1]
        payload = {'revision':original['revision'],'username':'operator','role':'admin','password':'OperatorDemo1'}
        for confirmation in (None, '', 'OtherPassword1'):
            status, body = self.request('create_user', {**payload,'confirmPassword':confirmation})
            self.assertEqual(status,422,body)
            self.assertEqual(self.request('users')[1],original)
        self.assertEqual(self.request('create_user',payload)[0],422)
        status, created = self.request('create_user',{**payload,'confirmPassword':payload['password'],'isPrimary':True})
        self.assertEqual(status,200,created)
        self.assertFalse(created['user']['isPrimary'])
        self.assertNotIn('passwordHash',created['user'])
        self.assertEqual(self.request('create_user',{**payload,'confirmPassword':payload['password'],'username':'stale'})[0],409)

    def test_primary_is_protected_and_other_accounts_cannot_reset_any_password(self):
        primary = next(user for user in self.request('users')[1]['users'] if user['isPrimary'])
        for username,role in [('operator','admin'),('viewer','user')]:
            revision = self.request('users')[1]['revision']
            status, created = self.request('create_user',{'revision':revision,'username':username,'password':'AccountDemo1','confirmPassword':'AccountDemo1','role':role})
            self.assertEqual(status,200,created)
        accounts = self.request('users')[1]
        protected = {'id':primary['id'],'revision':accounts['revision'],'username':primary['username'],'role':'user','enabled':False}
        self.assertEqual(self.request('update_user',protected)[0],409)
        self.assertEqual(self.request('delete_user',protected)[0],409)
        clients = {}
        for username in ('operator','viewer'):
            client,session = self.another_session(username,'AccountDemo1')
            clients[username] = (client,session)
            self.assertFalse(session['isPrimary'])
            for target in accounts['users']:
                status, body = self.request('change_password',{'id':target['id'],'revision':accounts['revision'],
                    'newPassword':'ForbiddenReset1','confirmPassword':'ForbiddenReset1','currentPassword':'AccountDemo1','isPrimary':True},
                    client=client,csrf=session['csrfToken'])
                self.assertEqual(status,403,(username,target['username'],body))
        operator = next(user for user in accounts['users'] if user['username']=='operator')
        viewer = next(user for user in accounts['users'] if user['username']=='viewer')
        client,session = clients['operator']
        status, body = self.request('update_user',{**viewer,'revision':accounts['revision'],'password':'BypassReset1'},client=client,csrf=session['csrfToken'])
        self.assertEqual(status,403,body)
        self.assertEqual(self.request('users')[1],accounts)
        reset = {'id':viewer['id'],'revision':accounts['revision'],'newPassword':'ViewerChanged1','confirmPassword':'ViewerChanged1'}
        self.assertEqual(self.request('change_password',dict(reset,confirmPassword='MismatchDemo1'))[0],422)
        self.assertEqual(self.request('change_password',reset,csrf=False)[0],403)
        status, changed = self.request('change_password',reset)
        self.assertEqual(status,200,changed)
        self.assertTrue(changed['isPrimary'])
        self.assertEqual(changed['username'],primary['username'])
        self.csrf = changed['csrfToken']
        self.assertEqual(self.request('runtime',client=clients['viewer'][0])[0],401)
        self.assertEqual(self.request('runtime',client=clients['operator'][0])[0],200)
        self.assertEqual(self.request('change_password',reset)[0],409)
        fresh,_ = self.another_session('viewer','ViewerChanged1')
        self.assertEqual(self.request('login',{'username':'viewer','password':'AccountDemo1'},client=fresh)[0],401)
        # Disabled accounts can be reset without enabling them or changing their role.
        revision = self.request('users')[1]['revision']
        self.assertEqual(self.request('update_user',{**operator,'revision':revision,'enabled':False})[0],200)
        revision = self.request('users')[1]['revision']
        status, changed = self.request('change_password',{'id':operator['id'],'revision':revision,'newPassword':'DisabledReset1','confirmPassword':'DisabledReset1'})
        self.assertEqual(status,200,changed)
        self.csrf = changed['csrfToken']
        after = next(user for user in self.request('users')[1]['users'] if user['id']==operator['id'])
        self.assertFalse(after['enabled'])
        self.assertEqual(after['role'],'admin')

    def test_primary_changes_own_password_without_old_password_and_revokes_old_sessions(self):
        other,_ = self.another_session('admin','FlugeDemo2026')
        config_before = (self.state/'config.json').read_bytes()
        primary = self.request('session')[1]
        revision = self.request('users')[1]['revision']
        reset = {'id':primary['userId'],'revision':revision,'newPassword':'PrimaryChanged1','confirmPassword':'PrimaryChanged1'}
        original = (self.state/'auth.json').read_bytes()
        for confirmation in ('MismatchDemo1',None):
            self.assertEqual(self.request('change_password',dict(reset,confirmPassword=confirmation))[0],422)
            self.assertEqual((self.state/'auth.json').read_bytes(),original)
        old_csrf = self.csrf
        status, changed = self.request('change_password',reset)
        self.assertEqual(status,200,changed)
        self.assertTrue(changed['authenticated'])
        self.assertTrue(changed['isPrimary'])
        self.assertNotEqual(changed['csrfToken'],old_csrf)
        self.csrf = changed['csrfToken']
        self.assertEqual(self.request('runtime')[0],200)
        self.assertEqual(self.request('runtime',client=other)[0],401)
        self.assertEqual((self.state/'config.json').read_bytes(),config_before)
        self.assertEqual(self.request('login',{'username':'admin','password':'FlugeDemo2026'},client=other)[0],401)
        self.another_session('admin','PrimaryChanged1')
        self.assertEqual(self.request('change_password',dict(reset,newPassword='AnotherReset1',confirmPassword='AnotherReset1'),csrf=old_csrf)[0],403)

    def test_legacy_primary_password_changes_do_not_modify_adguard_credentials(self):
        config_before = (self.state/'config.json').read_bytes()
        config = json.loads(config_before)
        (self.state/'auth.json').write_text('{"mode":"adguard"}')
        self.login(config['adguardUser'],config['adguardPass'])
        self.assertTrue(self.request('session')[1]['isPrimary'])
        self.assertEqual(self.request('save_config',{'configRevision':config['configRevision'],'adguardPass':'BypassReset1'})[0],409)
        status, changed = self.request('change_password',{'id':'adguard-admin','revision':0,'newPassword':'IndependentNew1','confirmPassword':'IndependentNew1'})
        self.assertEqual(status,200,changed)
        self.csrf = changed['csrfToken']
        self.assertTrue(changed['isPrimary'])
        self.assertEqual(changed['userId'],'independent-admin')
        self.assertEqual(changed['authMode'],'independent')
        self.assertEqual((self.state/'config.json').read_bytes(),config_before)
        self.assertEqual(self.request('runtime')[0],200)
        self.another_session(config['adguardUser'],'IndependentNew1')

    def ndi_settings(self, **changes):
        return dict({'enabled':True,'ip':'192.168.10.20','port':5959,'polling_rate':2,'timeout':3,'groups':'Public,camaras','mock_scenario':'normal'},**changes)

    def save_ndi(self, settings):
        revision = self.request('get_config')[1]['configRevision']
        return self.request('save_config',{'configRevision':revision,'ndi':settings})

    def apply_ndi_default(self, ip='', port=5959):
        subprocess.run([PHP, str(ROOT / 'cts/caddy/panel/bin/configure-ndi.php'), str(self.state), ip, str(port)],
                       capture_output=True, text=True, check=True)

    def test_discovery_deployment_sets_and_removes_the_automatic_destination(self):
        config = self.request('get_config')[1]
        self.apply_ndi_default()
        self.assertEqual(self.request('/api/ndi/status')[1], {'enabled': False})
        self.apply_ndi_default('192.168.1.16', 5974)
        applied = self.request('get_config')[1]
        self.assertEqual(applied['ndi'], self.ndi_settings(ip='192.168.1.16', port=5974, polling_rate=5, groups=''))
        self.assertEqual(applied['configRevision'], config['configRevision'])
        self.assertEqual(self.request('runtime')[1]['ndi'], applied['ndi'])
        stored = (self.state / 'config.json').read_bytes()
        self.apply_ndi_default('192.168.1.16', 5974)
        self.assertEqual((self.state / 'config.json').read_bytes(), stored)
        updated = self.request('save_config', {'configRevision': applied['configRevision'], 'installationName': 'Plato principal'})
        self.assertEqual(updated[0], 200)
        self.apply_ndi_default()
        self.assertEqual(self.request('/api/ndi/status')[1], {'enabled': False})
        self.assertEqual(self.request('get_config')[1]['installationName'], 'Plato principal')
        self.apply_ndi_default('192.168.1.16')
        self.assertTrue(self.request('runtime')[1]['ndi']['enabled'])

    def test_discovery_deployment_preserves_an_explicit_ndi_setting(self):
        self.apply_ndi_default('192.168.1.16')
        self.assertEqual(self.save_ndi(self.ndi_settings(enabled=False, ip='', polling_rate=5, groups=''))[0], 200)
        config = self.request('get_config')[1]
        self.apply_ndi_default('192.168.1.16', 5974)
        self.assertEqual(self.request('get_config')[1], config)
        self.apply_ndi_default()
        self.assertEqual(self.request('get_config')[1], config)

    def test_ndi_disabled_returns_immediately_without_creating_probe_state(self):
        self.assertFalse(self.request('get_config')[1]['ndi']['enabled'])
        for _ in range(3):
            status, body = self.request('/api/ndi/status')
            self.assertEqual((status,body),(200,{'enabled':False}))
        self.assertFalse((self.state/'ndi-cache.json').exists())
        self.assertEqual(list(self.state.glob('ndi-run-*')),[])
        guest = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.assertEqual(self.request('/api/ndi/status',client=guest)[0],401)

    def test_ndi_settings_validate_and_persist_without_losing_other_config(self):
        config = self.request('get_config')[1]
        settings = self.ndi_settings()
        for patch in [{'ip':''},{'ip':'999.1.1.1'},{'ip':'http://ndi.local'},{'ip':'ndi:5959'},
                      {'ip':'bad..local'},{'ip':'-ndi.local'},{'port':0},{'port':65536},{'port':'5959'},
                      {'polling_rate':1},{'polling_rate':301},{'timeout':0},{'timeout':16},
                      {'enabled':'true'},{'groups':'Public,\ncamaras'},{'groups':['Public']},{'mock_scenario':'invalid'}]:
            status, body = self.save_ndi({**settings,**patch})
            self.assertEqual(status,422,(patch,body))
            self.assertEqual(self.request('get_config')[1],config)
        status, saved = self.save_ndi({**settings,'ip':'ndi.example.local','port':5969,'groups':' Public, camaras,Public '})
        self.assertEqual(status,200,saved)
        canonical = self.request('get_config')[1]['ndi']
        self.assertEqual(canonical['ip'],'ndi.example.local')
        self.assertEqual(canonical['port'],5969)
        self.assertEqual(canonical['groups'],'Public,camaras')
        self.assertEqual(self.request('runtime')[1]['ndi'],canonical)
        self.assertTrue(self.request('runtime')[1]['ndi_mock'])
        self.assertEqual(self.request('get_config')[1]['scanNetworks'],config['scanNetworks'])
        self.preview.initialize(PHP,self.state)
        self.assertEqual(self.request('get_config')[1]['ndi'],canonical)

    def test_ndi_cache_rate_invalidation_and_disable(self):
        self.assertEqual(self.save_ndi(self.ndi_settings())[0],200)
        status, first = self.request('/api/ndi/status')
        self.assertEqual(status,200,first)
        self.assertTrue(first['online'])
        self.assertEqual(first['sources_count'],5)
        self.assertEqual(first['sources'][0],{'full_name':'CAM-01 (PTZ Plano General)','host':'CAM-01','stream':'PTZ Plano General','url':'192.168.10.51:5961'})
        self.assertEqual(self.request('/api/ndi/status')[1],first)
        path = self.state/'ndi-cache.json'
        cache = json.loads(path.read_text())
        self.assertEqual(cache['sequence'],1)
        cache['checked_at'] -= 3
        path.write_text(json.dumps(cache))
        second = self.request('/api/ndi/status')[1]
        self.assertNotEqual(first['latency_ms'],second['latency_ms'])
        self.assertEqual(json.loads(path.read_text())['sequence'],2)
        self.assertEqual(self.request('/api/ndi/refresh', {}, csrf=False)[0],403)
        self.assertEqual(self.request('/api/ndi/refresh', {}, client=urllib.request.build_opener(), csrf=False)[0],401)
        status, manual = self.request('/api/ndi/refresh', {})
        self.assertEqual(status,200,manual)
        self.assertNotEqual(second['latency_ms'],manual['latency_ms'])
        self.assertEqual(json.loads(path.read_text())['sequence'],3)
        self.assertEqual(self.request('/api/ndi/status')[1],manual)
        self.assertEqual(self.save_ndi({**self.ndi_settings(),'mock_scenario':'offline'})[0],200)
        self.assertFalse(self.request('/api/ndi/status')[1]['online'])
        cache_before = path.read_bytes()
        self.assertEqual(self.save_ndi({**self.ndi_settings(),'enabled':False})[0],200)
        self.assertEqual(self.request('/api/ndi/status')[1],{'enabled':False})
        self.assertEqual(self.request('/api/ndi/refresh', {}),(200,{'enabled':False}))
        self.assertEqual(path.read_bytes(),cache_before)

    def test_ndi_connection_test_scenarios_and_permissions(self):
        for scenario,online,count in [('normal',True,5),('offline',False,0),('empty',True,0),('missing_lib',True,0)]:
            status, result = self.request('/api/ndi/test',{**self.ndi_settings(),'mock_scenario':scenario})
            self.assertEqual(status,200,result)
            self.assertEqual((result['online'],result['sources_count']),(online,count))
            self.assertEqual(bool(result['warning']),scenario in ('offline','missing_lib'))
        self.assertFalse((self.state/'ndi-cache.json').exists(),'Connection tests must not populate the saved-status cache')
        self.assertFalse(self.request('get_config')[1]['ndi']['enabled'],'Tests must not implicitly enable or save the module')
        self.assertEqual(self.request('/api/ndi/test',self.ndi_settings(),csrf=False)[0],403)
        self.assertEqual(self.request('/api/ndi/test')[0],404)
        created = self.request('create_user',{'revision':0,'username':'ndiviewer','password':'ViewerDemo1','confirmPassword':'ViewerDemo1','role':'user'})[1]
        self.assertEqual(created['status'],'ok')
        client,session = self.another_session('ndiviewer','ViewerDemo1')
        self.assertEqual(self.request('/api/ndi/status',client=client)[1],{'enabled':False})
        self.assertEqual(self.request('/api/ndi/refresh',{},client=client,csrf=session['csrfToken']),(200,{'enabled':False}))
        self.assertEqual(self.request('/api/ndi/test',self.ndi_settings(),client=client,csrf=session['csrfToken'])[0],403)

    def ndi_refresh(self):
        path = self.state/'ndi-cache.json'
        if path.exists():
            cache = json.loads(path.read_text());cache['checked_at'] = 0;path.write_text(json.dumps(cache))
        return self.request('/api/ndi/status')[1]

    def test_runtime_install_requires_admin_csrf_and_acceptance_and_refreshes_status(self):
        self.assertEqual(self.save_ndi({**self.ndi_settings(), 'mock_scenario': 'missing_lib'})[0], 200)
        self.assertTrue(self.request('/api/ndi/status')[1]['runtime_missing'])
        self.assertEqual(self.request('ndi_runtime_prepare', {}, csrf=False)[0], 403)
        viewer = {'revision':0, 'username':'runtimeviewer', 'password':'ViewerDemo1', 'confirmPassword':'ViewerDemo1', 'role':'user'}
        self.assertEqual(self.request('create_user', viewer)[0], 200)
        client, session = self.another_session('runtimeviewer', 'ViewerDemo1')
        self.assertEqual(self.request('ndi_runtime_prepare', {}, client=client, csrf=session['csrfToken'])[0], 403)
        status, prepared = self.request('ndi_runtime_prepare', {})
        self.assertEqual(status, 200, prepared)
        self.assertIn('Licencia de la muestra', prepared['license'])
        self.assertEqual(self.request('ndi_runtime_install', {'package':prepared['package']})[0], 422)
        self.assertEqual(self.request('ndi_runtime_install', {'package':'a' * 64,'accepted':True})[0], 409)
        self.assertTrue(self.request('/api/ndi/status')[1]['runtime_missing'])
        self.assertEqual(self.request('ndi_runtime_install', {'package':prepared['package'],'accepted':True}), (200, {'installed':True}))
        result = self.request('/api/ndi/status')[1]
        self.assertEqual((result['runtime_missing'], result['sources_count'], result['receivers_count']), (False, 5, 2))
        self.assertEqual(self.request('ndi_runtime_prepare', {}), (200, {'installed':True}))

    def test_ndi_history_roles_last_seen_events_and_recovery_persist(self):
        self.assertEqual(self.save_ndi(self.ndi_settings())[0],200)
        first = self.ndi_refresh()
        self.assertEqual(first['receivers_count'],2)
        self.assertEqual(len(first['entities']),7)
        self.assertEqual(sum(item['direction']=='receiver' for item in first['entities']),2)
        self.assertTrue(all(item['device_role']=='both' for item in first['entities'] if item['host']=='VMIX-MAIN'))
        self.assertTrue(all(item['available'] is True for item in first['entities']))
        events = first['events']
        self.assertEqual(self.request('/api/ndi/status')[1]['events'],events,'Cache hits must not duplicate events')
        stable = self.ndi_refresh()
        self.assertEqual(stable['events'],events,'Stable probes must not flood the log')
        seen = {item['id']:item['last_seen'] for item in stable['entities']}
        self.assertEqual(self.save_ndi(self.ndi_settings(mock_scenario='empty'))[0],200)
        missing = self.ndi_refresh()
        self.assertEqual(len(missing['entities']),7,'Missing devices must remain available for inspection')
        self.assertTrue(all(item['available'] is False for item in missing['entities']))
        self.assertEqual({item['id']:item['last_seen'] for item in missing['entities']},seen)
        self.assertEqual(sum(event['type']=='entity_missing' for event in missing['events']),7)
        self.assertEqual(self.ndi_refresh()['events'],missing['events'])
        self.assertEqual(self.save_ndi(self.ndi_settings())[0],200)
        recovered = self.ndi_refresh()
        self.assertTrue(all(item['available'] is True for item in recovered['entities']))
        self.assertEqual(sum(event['type']=='entity_returned' for event in recovered['events']),7)
        self.assertEqual({item['id']:item['first_seen'] for item in recovered['entities']},{item['id']:item['first_seen'] for item in first['entities']})
        history_before = (self.state/'ndi-history.json').read_bytes()
        self.request('/api/ndi/test',self.ndi_settings())
        self.assertEqual((self.state/'ndi-history.json').read_bytes(),history_before,'Connection tests must not generate historical observations')
        self.preview.initialize(PHP,self.state)
        self.assertEqual((self.state/'ndi-history.json').read_bytes(),history_before)

    def test_ndi_clear_log_preserves_devices_scopes_cache_and_enforces_permissions(self):
        self.save_ndi(self.ndi_settings(groups='Other'))
        other = self.ndi_refresh()
        self.save_ndi(self.ndi_settings())
        first = self.ndi_refresh()
        self.assertTrue(first['events'])
        cache_before = json.loads((self.state/'ndi-cache.json').read_text())
        history_before = (self.state/'ndi-history.json').read_bytes()
        self.assertEqual(self.request('/api/ndi/log/clear',{},csrf=False)[0],403)
        self.assertEqual(self.request('/api/ndi/log/clear')[0],404)
        self.request('create_user',{'revision':0,'username':'logviewer','password':'ViewerDemo1','confirmPassword':'ViewerDemo1','role':'user'})
        viewer,session = self.another_session('logviewer','ViewerDemo1')
        self.assertEqual(self.request('/api/ndi/log/clear',{},client=viewer,csrf=session['csrfToken'])[0],403)
        self.assertEqual((self.state/'ndi-history.json').read_bytes(),history_before)
        status, cleared = self.request('/api/ndi/log/clear',{})
        self.assertEqual((status,cleared),(200,{'status':'ok','events':[]}))
        after = self.request('/api/ndi/status')[1]
        self.assertEqual(after['events'],[])
        self.assertEqual(after['entities'],first['entities'])
        self.assertEqual(after['server_last_seen'],first['server_last_seen'])
        cache_after = json.loads((self.state/'ndi-cache.json').read_text())
        self.assertEqual(cache_after['sequence'],cache_before['sequence'])
        self.assertEqual(cache_after['checked_at'],cache_before['checked_at'])
        self.assertEqual(self.ndi_refresh()['events'],[],'Stable probes must not resurrect cleared log entries')
        self.save_ndi(self.ndi_settings(groups='Other'))
        self.assertEqual(self.ndi_refresh()['events'],other['events'],'Other server/group histories must be preserved')
        self.save_ndi(self.ndi_settings())
        self.ndi_refresh()
        self.save_ndi(self.ndi_settings(enabled=False))
        self.assertEqual(self.request('/api/ndi/log/clear',{})[0],200)
        self.save_ndi(self.ndi_settings())
        self.assertEqual(self.request('/api/ndi/status')[1]['events'],[],'Re-enabling must not reuse an uncleared cached log')

    def test_ndi_forget_only_removes_absent_entity_in_selected_scope(self):
        self.save_ndi(self.ndi_settings(groups='Other'))
        other = self.ndi_refresh()
        self.save_ndi(self.ndi_settings())
        first = self.ndi_refresh()
        entity = next(item for item in first['entities'] if item['direction'] == 'receiver')
        action = 'ndi_forget_entity'
        payload = {'id': entity['id'], 'scope': first['history_scope']}
        self.assertEqual(self.request(action, payload)[0], 409)
        self.assertEqual(self.request(action, {**payload, 'id': 'invalid'})[0], 422)
        self.assertEqual(self.request(action, {**payload, 'scope': other['history_scope']})[0], 409)

        self.save_ndi(self.ndi_settings(mock_scenario='offline'))
        self.assertIsNone(next(item for item in self.ndi_refresh()['entities'] if item['id'] == entity['id'])['available'])
        self.assertEqual(self.request(action, payload)[0], 409)
        self.save_ndi(self.ndi_settings(mock_scenario='empty'))
        missing = self.ndi_refresh()
        self.assertEqual(self.request(action, payload, csrf=False)[0], 403)
        self.request('create_user', {'revision':0, 'username':'ndiviewer', 'password':'ViewerDemo1', 'confirmPassword':'ViewerDemo1', 'role':'user'})
        viewer, session = self.another_session('ndiviewer', 'ViewerDemo1')
        self.assertEqual(self.request(action, payload, client=viewer, csrf=session['csrfToken'])[0], 403)

        self.assertEqual(self.request(action, payload), (200, {'status':'ok'}))
        after = self.request('/api/ndi/status')[1]
        sightings = lambda status: {item['id']: (item['available'], item['last_seen']) for item in status['entities']}
        self.assertEqual(sightings(after), {key: value for key, value in sightings(missing).items() if key != entity['id']})
        self.assertEqual(after['events'], missing['events'])
        self.assertEqual(self.request(action, payload)[0], 200, 'An already removed entry stays removed on retry')
        self.assertEqual(sightings(self.ndi_refresh()), sightings(after))

        self.save_ndi(self.ndi_settings(groups='Other'))
        self.assertEqual({item['id']: item['first_seen'] for item in self.ndi_refresh()['entities']},
                         {item['id']: item['first_seen'] for item in other['entities']})
        self.save_ndi(self.ndi_settings())
        rediscovered = self.ndi_refresh()
        self.assertEqual(rediscovered['receivers_count'], 2)
        self.assertTrue(next(item for item in rediscovered['entities'] if item['id'] == entity['id'])['available'])
        self.assertEqual(self.request(action, payload)[0], 409, 'A source rediscovered while the dialog was open cannot be removed')

    def test_ndi_history_does_not_call_devices_offline_when_observation_fails(self):
        self.save_ndi(self.ndi_settings())
        first = self.ndi_refresh()
        for scenario in ('offline','missing_lib'):
            self.save_ndi(self.ndi_settings(mock_scenario=scenario))
            result = self.ndi_refresh()
            self.assertTrue(all(item['available'] is None for item in result['entities']))
            self.assertEqual([item['last_seen'] for item in result['entities']],[item['last_seen'] for item in first['entities']])
            self.assertFalse(any(event['type']=='entity_missing' for event in result['events']))
        self.assertEqual(result['server_last_seen'],result['last_updated'])
        self.save_ndi(self.ndi_settings(enabled=False))
        before = (self.state/'ndi-history.json').read_bytes()
        self.assertEqual(self.ndi_refresh(),{'enabled':False})
        self.assertEqual((self.state/'ndi-history.json').read_bytes(),before)

    def test_ndi_history_scopes_groups_and_limits_event_retention(self):
        self.save_ndi(self.ndi_settings())
        first = self.ndi_refresh()
        self.save_ndi(self.ndi_settings(groups='Other',mock_scenario='empty'))
        other = self.ndi_refresh()
        self.assertEqual(other['entities'],[],'A different group query must not mark previous devices unavailable')
        self.assertFalse(any(event['type']=='entity_missing' for event in other['events']))
        self.save_ndi(self.ndi_settings())
        restored = self.ndi_refresh()
        self.assertEqual({item['id']:item['first_seen'] for item in restored['entities']},{item['id']:item['first_seen'] for item in first['entities']})
        path = self.state/'ndi-history.json'
        history = json.loads(path.read_text())
        scope = next(value for value in history['scopes'].values() if len(value['entities'])==7)
        scope['events'] = [dict(first['events'][0],id=str(index)) for index in range(520)]
        path.write_text(json.dumps(history))
        limited = self.ndi_refresh()
        self.assertEqual(len(limited['events']),500)

    def test_scanner_is_bounded_and_demo_contains_companion_ndi(self):
        status, data = self.request('scan_chunk', {'ips':['192.168.1.15','192.168.1.16','192.168.1.30']})
        self.assertEqual(status, 200)
        self.assertTrue(data['demo'])
        self.assertEqual(data['results'][0]['ports'], [8000,22])
        self.assertEqual(data['results'][1]['ports'], [5959])
        for ips in (['8.8.8.8'],['10.0.0.1'],['192.168.1.30']*33):
            self.assertEqual(self.request('scan_chunk', {'ips':ips})[0], 422)

    def test_reinitialize_and_cli_export_preserve_web_inventory(self):
        data = self.inventory()
        data['services'][0]['name'] = 'Changed in browser'
        self.assertEqual(self.request('save_data',data)[0],200)
        self.preview.initialize(PHP, self.state)
        self.assertEqual(self.inventory()['services'][0]['name'],'Changed in browser')
        result = subprocess.run([PHP,str(ROOT/'cts/caddy/panel/bin/export.php')], env=dict(os.environ,FLUGE_STATE_DIR=str(self.state)),capture_output=True,text=True,check=True)
        exported = json.loads(result.stdout)
        self.assertEqual(exported[0]['name'],'Changed in browser')
        self.assertEqual(exported[-1]['mode'],'dns_only')

    def test_config_does_not_expose_password_and_rejects_stale_revision(self):
        config = self.request('get_config')[1]
        self.assertNotIn('adguardPass',config)
        status, body = self.request('save_config', {'configRevision':config['configRevision'],'adguardPass':'UpdatedSecret1'})
        self.assertEqual(status,200,body)
        self.assertNotIn('adguardPass',body['config'])
        self.assertTrue(body['dns_sync']['ok'])
        self.assertEqual(body['dns_sync']['configRevision'],body['configRevision'])
        self.assertEqual(self.request('runtime')[1]['lastSync']['configRevision'],body['configRevision'])
        self.assertEqual(self.request('save_config', {'configRevision':config['configRevision'],'adguardUser':'another'})[0],409)

    def test_installation_name_is_shared_validated_and_independent_of_inventory(self):
        original = self.inventory()
        config = self.request('get_config')[1]
        self.assertEqual(config['installationName'],'')
        status, saved = self.request('save_config',{'configRevision':config['configRevision'],'installationName':'  Plató principal · Fluge  '})
        self.assertEqual(status,200,saved)
        self.assertEqual(saved['config']['installationName'],'Plató principal · Fluge')
        self.assertTrue(saved['dns_sync']['ok'])
        self.assertEqual(self.request('runtime')[1]['installationName'],'Plató principal · Fluge')
        self.assertEqual(self.inventory(),original)
        revision = saved['configRevision']
        for name in ('x'*81,'Dos\nlíneas',42,None):
            self.assertEqual(self.request('save_config',{'configRevision':revision,'installationName':name})[0],422)
        self.assertEqual(self.request('get_config')[1]['configRevision'],revision)
        self.assertEqual(self.request('save_config',{'configRevision':config['configRevision'],'installationName':'Old revision'})[0],409)
        status, body = self.request('create_user',{'revision':0,'username':'nameviewer','password':'ViewerDemo1','confirmPassword':'ViewerDemo1','role':'user','enabled':True})
        self.assertEqual(status,200,body)
        self.request('logout',{})
        self.login('nameviewer','ViewerDemo1')
        self.assertEqual(self.request('runtime')[1]['installationName'],'Plató principal · Fluge')
        self.assertEqual(self.request('save_config',{'configRevision':revision,'installationName':'Forbidden'})[0],403)

    def test_installation_name_supports_unicode_and_can_be_cleared(self):
        revision = self.request('get_config')[1]['configRevision']
        name = 'ó'*80
        status, body = self.request('save_config',{'configRevision':revision,'installationName':name})
        self.assertEqual(status,200,body)
        self.assertEqual(self.request('runtime')[1]['installationName'],name)
        status, body = self.request('save_config',{'configRevision':body['configRevision'],'installationName':''})
        self.assertEqual(status,200,body)
        self.assertEqual(self.request('runtime')[1]['installationName'],'')

    def test_all_services_can_be_removed_without_invalid_export(self):
        data = self.inventory()
        data['services'] = []
        status, saved = self.request('save_data',data)
        self.assertEqual(status,200)
        self.assertTrue(saved['dns_sync']['ok'])
        result = subprocess.run([PHP,str(ROOT/'cts/caddy/panel/bin/export.php')],env=dict(os.environ,FLUGE_STATE_DIR=str(self.state)),capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout),[])

    def test_multiple_networks_preserve_legacy_settings_and_share_individual_pools(self):
        current = self.request('get_config')[1]
        self.assertEqual(len(current['scanNetworks']),1)
        self.assertEqual(current['scanNetworks'][0]['cidr'],'192.168.1.0/24')
        self.assertEqual(current['scanNetworks'][0]['dhcpPool'],{'start':'192.168.1.100','end':'192.168.1.250'})
        self.assertNotIn('scanNetworks',json.loads((self.state/'config.json').read_text(encoding='utf-8')))
        networks = [
            {'id':'control','name':'Control','cidr':'192.168.1.22/24','dhcpPool':{'start':'192.168.1.100','end':'192.168.1.240'}},
            {'id':'video','name':'Vídeo','cidr':'192.168.20.0/24','dhcpPool':{'start':'192.168.20.50','end':'192.168.20.99'}},
            {'id':'lighting','name':'Iluminación','cidr':'10.20.0.0/16','dhcpPool':None}]
        status, saved = self.request('save_config',{'configRevision':current['configRevision'],'scanNetworks':networks})
        self.assertEqual(status,200,saved)
        self.assertTrue(saved['dns_sync']['ok'])
        scanner = self.request('get_scanner_config')[1]
        self.assertEqual(scanner['scanAllowedCidrs'],['192.168.1.0/24','192.168.20.0/24','10.20.0.0/16'])
        self.assertEqual([p['label'] for p in scanner['scanPresets']],['Control','Vídeo','Iluminación'])
        self.assertEqual(scanner['scanNetworks'][1]['dhcpPool'],networks[1]['dhcpPool'])
        self.assertIsNone(scanner['scanNetworks'][2]['dhcpPool'])
        self.assertEqual(self.request('scan_chunk',{'ips':['192.168.20.70']})[0],200)
        self.assertEqual(self.request('scan_chunk',{'ips':['192.168.21.70']})[0],422)
        self.assertEqual(self.request('save_config',{'configRevision':current['configRevision'],'scanNetworks':networks})[0],409)
        original = self.inventory()
        status, saved = self.request('save_config',{'configRevision':saved['configRevision'],'scanNetworks':[networks[2]]})
        self.assertEqual(status,200,saved)
        self.assertIsNone(saved['config']['dhcpRange'])
        self.assertEqual(self.request('scan_chunk',{'ips':['192.168.20.70']})[0],422)
        self.assertEqual(self.inventory(),original)

    def test_networks_reject_invalid_ranges_pools_and_duplicates_without_overwriting(self):
        config = self.request('get_config')[1]
        good = {'id':'control','name':'Control','cidr':'192.168.1.0/24','dhcpPool':{'start':'192.168.1.100','end':'192.168.1.200'}}
        invalid = [[], 'bad', [dict(good,cidr='8.8.8.0/24')],
            [dict(good,dhcpPool={'start':'192.168.2.100','end':'192.168.2.200'})],
            [dict(good,dhcpPool={'start':'192.168.1.200','end':'192.168.1.100'})],
            [dict(good,dhcpPool={'start':'192.168.1.100','end':''})],
            [good,dict(good,id='other',name='Other',cidr='192.168.1.99/24')],
            [good,dict(good,cidr='192.168.2.0/24',name='Other',dhcpPool=None)],
            [good,dict(good,id='other',cidr='192.168.2.0/24',name='CONTROL',dhcpPool=None)]]
        for networks in invalid:
            status, body = self.request('save_config',{'configRevision':config['configRevision'],'scanNetworks':networks})
            self.assertEqual(status,422,(networks,body))
            self.assertEqual(self.request('get_config')[1],config)

    def test_disabled_dns_and_external_conflicts_report_partial_publication(self):
        (self.state / 'demo-dns-settings.json').write_text('{"enabled":false}')
        draft = self.inventory()
        draft['services'][0]['description'] = 'Saved despite DNS failure'
        status, body = self.request('save_data', draft)
        self.assertEqual(status,200)
        self.assertEqual(body['status'],'partial')
        self.assertEqual(self.inventory()['services'][0]['description'],'Saved despite DNS failure')
        revision = body['revision']
        self.assertTrue(body['dns_sync']['caddy_updated'])
        self.assertFalse(body['dns_sync']['ok'])
        (self.state / 'demo-dns-settings.json').unlink()
        self.assertEqual(self.request('sync_infrastructure', {'revision':revision})[0],200)
        (self.state / 'demo-dns.json').write_text('[{"domain":"proxmox.lan","answer":"192.168.1.99"}]')
        status, body = self.request('sync_infrastructure', {'revision':revision})
        self.assertEqual(status,502)
        self.assertTrue(any('proxmox.lan' in e for e in body['dns_sync']['errors']))
        self.assertIn({'domain':'proxmox.lan','answer':'192.168.1.99'},self.request('applied')[1]['dns'])

    def test_host_dns_handover_remains_deletable_from_the_panel(self):
        data = self.inventory()
        next(s for s in data['services'] if s['proxyMode']=='dns_only')['ip'] = '192.168.1.46'
        self.assertEqual(self.request('save_data',data)[0],200)
        ownership=json.loads((self.state/'managed-dns.json').read_text(encoding='utf-8'))
        (self.state/'managed-dns.json').write_text(json.dumps([e for e in ownership if e['domain']!='ndi.lan']))
        entries = [{'domain':'ndi.lan','answer':'192.168.1.46'}, {'domain':'manual.lan','answer':'192.168.1.99'}]
        (self.state/'demo-dns.json').write_text(json.dumps(entries))
        subprocess.run([PHP,str(ROOT/'cts/caddy/panel/bin/adopt-dns.php')],input=json.dumps({'rewrites':entries}),env=dict(os.environ,FLUGE_STATE_DIR=str(self.state)),text=True,check=True)
        data=self.inventory()
        data['services']=[s for s in data['services'] if s['proxyMode']!='dns_only']
        saved=self.request('save_data',data)[1]
        self.assertTrue(saved['dns_sync']['ok'])
        dns=self.request('applied')[1]['dns']
        self.assertFalse(any(e['domain']=='ndi.lan' for e in dns))
        self.assertIn({'domain':'manual.lan','answer':'192.168.1.99'},dns)

    def test_production_mode_requires_private_credentials_and_disables_demo(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            seed = json.loads((self.state/'seed.json').read_text(encoding='utf-8'))
            config = json.loads((self.state/'initial-config.json').read_text(encoding='utf-8'))
            password = 'Release1' + secrets.token_hex(16)
            subprocess.run([PHP,str(ROOT/'cts/caddy/panel/bin/initialize.php'),directory,'--stdin'],
                input=json.dumps([seed,config,{'username':'prod-admin','password':password}]),text=True,capture_output=True,check=True)
            with socket.socket() as listener:
                listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
            with (state/'server.log').open('wb') as log:
                server = subprocess.Popen([PHP,'-d',f'session.save_path={state}','-S',f'127.0.0.1:{port}','-t',str(ROOT/'cts/caddy/panel/public')],
                    env=dict(os.environ,FLUGE_DEMO='0',NDI_MOCK='false',FLUGE_STATE_DIR=directory),stdout=log,stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                try:
                    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                    base = f'http://127.0.0.1:{port}'
                    for _ in range(100):
                        try:
                            with client.open(base,timeout=.3) as response:html=response.read().decode()
                            break
                        except OSError:time.sleep(.05)
                    else:self.fail('Production-mode PHP did not start')
                    def call(action, payload=None):
                        request=urllib.request.Request(base+'/api.php'+('?action='+action if payload is None else ''),
                            data=None if payload is None else json.dumps({'action':action,**payload}).encode(),headers={'Content-Type':'application/json'})
                        try:
                            with client.open(request,timeout=3) as response:return response.status,json.load(response)
                        except urllib.error.HTTPError as error:return error.code,json.load(error)
                    self.assertNotIn('FlugeDemo2026',html)
                    self.assertFalse(call('session')[1]['demo'])
                    self.assertEqual(call('data')[0],401)
                    self.assertEqual(call('login',{'username':'admin','password':'FlugeDemo2026'})[0],401)
                    status,session = call('login',{'username':'prod-admin','password':password})
                    self.assertEqual(status,200,session)
                    runtime = call('runtime')[1]
                    self.assertFalse(runtime['demo'])
                    self.assertFalse(runtime['ndi_mock'])
                    self.assertFalse(runtime['ndi']['enabled'])
                    self.assertEqual(call('ndi_status')[1],{'enabled':False})
                    self.assertEqual(call('applied')[0],404)
                    with client.open(base+'/app.js') as response:self.assertNotIn('FlugeDemo2026',response.read().decode())
                finally:
                    server.terminate();server.wait(timeout=10)

    def test_native_installer_accepts_private_bootstrap_via_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            seed=json.loads((self.state/'seed.json').read_text(encoding='utf-8'))
            config=json.loads((self.state/'initial-config.json').read_text(encoding='utf-8'))
            subprocess.run([PHP,str(ROOT/'cts/caddy/panel/bin/initialize.php'),directory,'--stdin'],
                input=json.dumps([seed,config,{'username':'admin','password':'FlugeDemo2026'}]),text=True,capture_output=True,check=True)
            inventory=json.loads((Path(directory)/'inventory.json').read_text(encoding='utf-8'))
            self.assertEqual(len(inventory['services']),4)
            self.assertEqual(inventory['revision'],1)
            auth=json.loads((Path(directory)/'auth.json').read_text(encoding='utf-8'))
            self.assertEqual(auth['username'],'admin')
            self.assertNotEqual(auth['passwordHash'],'FlugeDemo2026')
