import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_updates
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

    def test_panel_compares_deployed_revision_to_public_branch(self):
        deployed, published = 'a' * 40, 'b' * 40
        with (patch.object(check_updates, 'github_origin', return_value='example/panel'),
              patch.object(check_updates, 'get_json') as fetch):
            for remote, expected in [('ahead', 'update'), ('behind', 'newer'), ('diverged', 'diverged')]:
                fetch.side_effect = [{'sha': published}, {'status': remote}]
                report = check_updates.compare_panel('.', {'commit': deployed})
                self.assertEqual(report['installed'], deployed)
                self.assertEqual(report['latest'], published)
                self.assertEqual(report['status'], expected)
            fetch.side_effect = [{'sha': deployed}]
            self.assertEqual(check_updates.compare_panel('.', {'commit': deployed, 'dirty': True})['status'], 'local_changes')

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
