import contextlib
from hashlib import sha256
import io
import json
import tempfile
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import call, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_updates
from panel_release import bump_release, validate_release
import run_updates


class UpdatesTests(unittest.TestCase):
    def test_worker_accepts_only_versions_for_known_components(self):
        job = {'id': 'd' * 32, 'role': 'panel', 'version': 'b' * 40, 'created': 10}
        self.assertTrue(run_updates.valid_request(job, 11))
        self.assertFalse(run_updates.valid_request({**job, 'role': 'host'}, 11))
        self.assertFalse(run_updates.valid_request({**job, 'version': 'main'}, 11))
        self.assertFalse(run_updates.valid_request({**job, 'role': 'caddy', 'version': '2.11.9 --bad-option'}, 11))
        self.assertFalse(run_updates.valid_request({**job, 'created': 12}, 11))
        self.assertTrue(run_updates.valid_request({**job, 'role': 'ndi', 'version': 'SDK 6.4.0'}, 11))

    def test_worker_revalidates_selected_vendor_version_before_running(self):
        with (patch.object(run_updates, 'verify_target', return_value='112'),
              patch.object(run_updates, 'guest', return_value='5.8.0'),
              patch.object(run_updates, 'compare_product', return_value={'latest':'5.10.0','status':'update'}),
              patch.object(run_updates, 'update_product') as update):
            with self.assertRaises(ValueError):
                run_updates.perform('.', {'role':'companion','version':'5.9.0'})
            update.assert_not_called()
            run_updates.perform('.', {'role':'companion','version':'5.10.0'})
            update.assert_called_once_with('112', 'companion', '5.10.0')

    def test_worker_checks_versions_without_installing_and_records_failure(self):
        job = {'id': 'd' * 32, 'role': 'check', 'created': 10}
        self.assertFalse(run_updates.valid_request({**job, 'version': 'anything'}, 11))
        command = ('bash', run_updates.SNAPSHOT / 'updates.sh', '--scheduled')
        for state, error in [('success', None), ('failure', subprocess.CalledProcessError(1, command))]:
            with (self.subTest(state=state), contextlib.redirect_stderr(io.StringIO()),
                  patch.object(sys, 'argv', ['run_updates.py', '.', '111']),
                  patch.object(run_updates.time, 'time', return_value=11),
                  patch.object(run_updates, 'read_guest_json', side_effect=[job, {}]),
                  patch.object(run_updates, 'perform') as install,
                  patch.object(run_updates, 'execute', side_effect=error) as execute,
                  patch.object(run_updates, 'job_state') as record):
                run_updates.main()
                install.assert_not_called()
                execute.assert_called_once_with(*command, pass_fds=(9,))
                self.assertEqual(record.call_args_list, [call('111', job, 'running'), call('111', job, state)])

    def test_panel_does_not_deploy_from_a_checkout_ahead_of_selected_commit(self):
        target = 'b' * 40
        with (patch.object(run_updates, 'read_guest_json', return_value={'commit':'a' * 40}),
              patch.object(run_updates, 'compare_panel', return_value={'latest':target, 'status':'update'}),
              patch.object(run_updates, 'github_origin', return_value='example/panel'),
              patch.object(run_updates, 'execute', side_effect=['', None, target, None, 'c' * 40]) as execute):
            with self.assertRaisesRegex(ValueError, 'por delante'):
                run_updates.update_panel(Path('.'), '111', target)
            self.assertEqual(execute.call_count, 5)
            self.assertTrue(all(call.args[0] == 'git' for call in execute.call_args_list))

    def test_panel_deploys_the_verified_release_tag(self):
        deployed, published = 'a' * 40, 'b' * 40
        with (patch.object(run_updates, 'read_guest_json', side_effect=[{'commit':deployed}, {'commit':published}]),
              patch.object(run_updates, 'compare_panel', return_value={'latest':published, 'tag':'panel-v0.0.3', 'status':'update',
                  'latestRelease':{'version':'0.0.3', 'type':'alpha', 'update_required':False}}),
              patch.object(run_updates, 'github_origin', return_value='example/panel'),
              patch.object(run_updates, 'execute', side_effect=['', None, published, None, published, None]) as execute):
            run_updates.update_panel(Path('.'), '111', published)
            self.assertEqual(execute.call_args_list[1].args[-1], 'refs/tags/panel-v0.0.3')
            self.assertEqual(execute.call_args_list[2].args[-1], 'FETCH_HEAD^{commit}')
            self.assertEqual(execute.call_args_list[-1].args, ('bash', Path('sync.sh'), '--ct', 'caddy', '--yes'))
            supplied = json.loads(execute.call_args_list[-1].kwargs['env']['FLUGE_PANEL_RELEASE'])
            self.assertEqual(supplied['commit'], published)
            self.assertEqual(supplied['release']['version'], '0.0.3')

    def test_panel_reads_attached_manifest_pinned_to_tag(self):
        installed, published = 'a' * 40, 'b' * 40
        release = {'version':'0.0.6', 'type':'alpha', 'update_required':False}
        publication = {'tag_name':'panel-v0.0.6', 'assets':[{'name':'panel-version.json', 'state':'uploaded'}]}
        with (patch.object(check_updates, 'github_origin', return_value='example/panel'),
              patch.object(check_updates, 'get_json') as fetch):
            fetch.side_effect = [publication, {'sha':published}, {**release, 'commit':published}, {'status':'ahead'}]
            report = check_updates.compare_panel('.', {'commit':installed})
            self.assertEqual(report['status'], 'update')
            self.assertEqual(report['latestRelease'], release)
            self.assertEqual(fetch.call_args_list[2].args[0],
                             'https://github.com/example/panel/releases/download/panel-v0.0.6/panel-version.json')
            fetch.side_effect = [publication, {'sha':published}, {**release, 'commit':installed}]
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(check_updates.compare_panel('.', {'commit':installed})['status'], 'unknown_remote')

    def test_deployment_resolves_published_or_supplied_manifest_without_repository_file(self):
        commit = 'a' * 40
        release = {'version':'0.0.6', 'type':'alpha', 'update_required':False}
        publication = {'tag_name':'panel-v0.0.6', 'assets':[{'name':'panel-version.json', 'state':'uploaded'}]}
        with (tempfile.TemporaryDirectory() as checkout,
              patch.object(check_updates, 'github_origin', return_value='example/panel'),
              patch.object(check_updates.subprocess, 'check_output', return_value='panel-v0.0.6\n'),
              patch.object(check_updates, 'get_json') as fetch):
            self.assertEqual(check_updates.deployment_release(checkout, commit, {'commit':commit, 'release':release}), release)
            fetch.assert_not_called()
            fetch.side_effect = [publication, {'sha':commit}, {**release, 'commit':commit}]
            self.assertEqual(check_updates.deployment_release(checkout, commit), release)
            with self.assertRaisesRegex(ValueError, 'checkout'):
                check_updates.deployment_release(checkout, commit, {'commit':'b' * 40, 'release':release})

    def test_panel_compares_deployed_revision_to_published_release(self):
        deployed, published = 'a' * 40, 'b' * 40
        installed_release = {'version':'0.0.1','type':'test','update_required':False}
        new_release = {'version':'0.0.2','type':'stable','update_required':False}
        tagged = {'tag_name':'panel-v0.0.2'}
        revision = {'commit':deployed,'release':installed_release}
        with (patch.object(check_updates, 'github_origin', return_value='example/panel'),
              patch.object(check_updates, 'get_json') as fetch):
            for remote, expected in [('ahead', 'update'), ('behind', 'newer'), ('diverged', 'diverged')]:
                fetch.side_effect = [tagged, {'sha': published}, new_release, {'status': remote}]
                report = check_updates.compare_panel('.', revision)
                self.assertEqual(report['installed'], deployed)
                self.assertEqual(report['latest'], published)
                self.assertEqual(report['installedRelease'], installed_release)
                self.assertEqual(report['latestRelease'], new_release)
                self.assertEqual(report['tag'], 'panel-v0.0.2')
                self.assertEqual(report['status'], expected)
                self.assertTrue(fetch.call_args_list[-3].args[0].endswith('/commits/panel-v0.0.2'))
                self.assertIn(f'/contents/panel-version.json?ref={published}', fetch.call_args_list[-2].args[0])
            fetch.side_effect = [tagged, {'sha': deployed}, new_release]
            self.assertEqual(check_updates.compare_panel('.', revision)['status'], 'current')
            fetch.side_effect = [tagged, {'sha': deployed}, new_release]
            self.assertEqual(check_updates.compare_panel('.', {**revision, 'dirty': True})['status'], 'local_changes')

    def test_panel_follows_published_minimums_and_blocks_missing_steps(self):
        installed, older, bridge, latest = [letter * 40 for letter in 'abcd']
        legacy = {'version':'1.5.0', 'type':'stable', 'update_required':False}
        target = {**legacy, 'version':'2.0.1', 'minimum_version':'1.9.1'}
        intermediate = {**legacy, 'version':'1.9.1', 'minimum_version':'1.8.0'}
        previous = {**legacy, 'version':'1.8.0'}
        def remote(url, *_):
            path = url.split('example/panel/', 1)[1]
            if path == 'releases/latest':
                return {'tag_name':'panel-v2.0.1'}
            if path.startswith('releases/tags/'):
                return {'tag_name':path.split('/')[-1]}
            if path.startswith('commits/'):
                return {'sha':{'panel-v2.0.1':latest, 'panel-v1.9.1':bridge, 'panel-v1.8.0':older}[path.split('/')[-1]]}
            if path.startswith('contents/'):
                return {latest:target, bridge:intermediate, older:previous}[path.split('ref=')[1]]
            self.assertTrue(path.startswith('compare/'))
            return {'status':'ahead'}
        revision = {'commit':installed, 'release':legacy}
        with (patch.object(check_updates, 'github_origin', return_value='example/panel'),
              patch.object(check_updates, 'get_json', side_effect=remote) as fetch):
            report = check_updates.compare_panel('.', revision)
            self.assertEqual(report['status'], 'update')
            self.assertEqual([step['version'] for step in report['upgradePath']], ['1.8.0','1.9.1','2.0.1'])
            report = check_updates.compare_panel('.', {'commit':bridge, 'release':intermediate})
            self.assertEqual(report['status'], 'update')
            self.assertNotIn('upgradePath', report)
            report = check_updates.compare_panel('.', {'commit':installed})
            self.assertEqual(report['status'], 'incompatible')
            def missing(url, *args):
                if url.endswith('/releases/tags/panel-v1.8.0'):
                    raise HTTPError(url, 404, 'No existe', {}, None)
                return remote(url, *args)
            fetch.side_effect = missing
            report = check_updates.compare_panel('.', revision)
            self.assertEqual(report['status'], 'incompatible')
            self.assertIn('1.8.0', report['compatibility'])
            self.assertNotIn('upgradePath', report)

    def test_worker_validates_full_path_and_stops_when_an_intermediate_step_fails(self):
        installed, first, bridge, latest = [letter * 40 for letter in 'abcd']
        steps = [{'version':'1.8.0', 'commit':first, 'tag':'panel-v1.8.0'},
                 {'version':'1.9.1', 'commit':bridge, 'tag':'panel-v1.9.1'},
                 {'version':'2.0.1', 'commit':latest, 'tag':'panel-v2.0.1'}]
        report = {'status':'update', 'latest':latest, 'upgradePath':steps}
        job = {'planHash':sha256(f'{first},{bridge},{latest}'.encode()).hexdigest()}
        checkout = Path('.')
        with (patch.object(run_updates, 'read_guest_json', return_value={'commit':installed}),
              patch.object(run_updates, 'compare_panel', return_value=report),
              patch.object(run_updates, 'github_origin', return_value='example/panel'),
              patch.object(run_updates, 'execute', side_effect=['', latest]) as execute,
              patch.object(run_updates, 'is_ancestor', side_effect=[True, True, True, False, True]),
              patch.object(run_updates, 'fetch_panel_step') as fetch,
              patch.object(run_updates, 'job_state') as state,
              patch.object(run_updates, 'sync_panel_worktree', side_effect=[None, ValueError('fallo')]) as sync):
            with self.assertRaisesRegex(ValueError, 'ruta de actualización cambió'):
                run_updates.update_panel(checkout, '111', latest, {})
            fetch.assert_not_called()
            sync.assert_not_called()
            execute.side_effect = ['', latest]
            with self.assertRaisesRegex(ValueError, 'fallo'):
                run_updates.update_panel(checkout, '111', latest, job)
            self.assertEqual([c.args[-1] for c in fetch.call_args_list], steps)
            self.assertEqual([c.args[-1] for c in sync.call_args_list], [first, bridge])
            self.assertIn('Instalando 1.8.0', state.call_args_list[0].args[1]['message'])
            self.assertIn('Instalando 1.9.1', state.call_args_list[1].args[1]['message'])

    def test_installer_only_commit_is_ignored(self):
        deployed, published = 'a' * 40, 'b' * 40
        release = {'version':'0.0.1','type':'test','update_required':True}
        missing = HTTPError('https://api.github.com', 404, 'Sin publicaciones', {}, None)
        with (patch.object(check_updates, 'github_origin', return_value='example/panel'),
              patch.object(check_updates, 'get_json', side_effect=[missing, {'sha':published}, {**release,'update_required':False}]) as fetch):
            entry = check_updates.compare_panel('.', {'commit':deployed, 'release':release})
            self.assertEqual(entry['status'], 'current')
            self.assertIsNone(entry['latest'])
            self.assertEqual(entry['latestRelease'], release)
            self.assertEqual(fetch.call_count, 3)
            self.assertTrue(fetch.call_args_list[1].args[0].endswith('/commits/HEAD'))

    def test_publication_increments_each_part_and_sets_required(self):
        release = {'version':'0.1.9', 'type':'test', 'update_required':True}
        for increment, expected in [('parche', '0.1.10'), ('menor', '0.2.0'), ('mayor', '1.0.0')]:
            self.assertEqual(bump_release(release, increment, False),
                             {'version':expected, 'type':'test', 'update_required':False})
        release = {**release, 'minimum_version':'0.1.2'}
        self.assertEqual(bump_release(release, 'parche', False)['minimum_version'], '0.1.2')
        self.assertEqual(bump_release(release, 'parche', False, '0.1.9')['minimum_version'], '0.1.9')
        self.assertNotIn('minimum_version', bump_release(release, 'parche', False, 'ninguna'))
        with self.assertRaisesRegex(ValueError, 'mínima debe ser anterior'):
            validate_release({**release, 'minimum_version':'0.1.9'})

    def test_publication_type_is_independent_of_version_and_required(self):
        release = {'version':'0.0.5', 'type':'alpha', 'update_required':True}
        for selected in ('conservar', 'hotfix', 'alpha', 'beta', 'oficial'):
            with self.subTest(selected=selected):
                self.assertEqual(bump_release(release, 'parche', False, build_type=selected),
                                 {'version':'0.0.6', 'type':'alpha' if selected == 'conservar' else selected, 'update_required':False})

    def test_products_compare_stable_versions_and_keep_partial_results(self):
        with patch.object(check_updates, 'stable_version', return_value='v5.10.0') as fetch:
            self.assertEqual(check_updates.compare_product('companion', 'available', '5.9.0+123-stable-abc')['status'], 'update')
            self.assertEqual(check_updates.compare_product('companion', 'available', '5.10.0')['status'], 'current')
            self.assertEqual(check_updates.compare_product('companion', 'unavailable', '')['status'], 'unavailable')
            ndi = check_updates.compare_product('ndi', 'available', '/opt/ndi-discovery/lib/libndi.so.6.3.1')
            self.assertEqual((ndi['installed'], ndi['status']), ('SDK 6.3.1', 'manual'))
            self.assertEqual(fetch.call_count, 2)
            fetch.side_effect = OSError('offline')
            with contextlib.redirect_stderr(io.StringIO()):
                caddy = check_updates.compare_product('caddy', 'available', 'v2.11.9 h1:...')
            self.assertEqual((caddy['installed'], caddy['status']), ('2.11.9', 'unknown_remote'))
