import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from inventory import CT_ROLES, ConfigError, load_services, role_networks, settings


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def example_env():
    env = {}
    for line in (ROOT / "config.env.example").read_text(encoding="utf-8").splitlines():
        fields = shlex.split(line, comments=True)
        if fields:
            key, value = fields[0].split("=", 1)
            env[key] = value
    return env


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.env = example_env()
        self.cfg = settings(self.env)
        self.data = json.loads((ROOT / "services.json").read_text(encoding="utf-8"))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "services.json"

    def load(self, data=None):
        self.path.write_text(json.dumps(self.data if data is None else data), encoding="utf-8")
        return load_services(self.path, self.cfg)

    def test_rendered_routes_and_dns_agree_with_changed_network(self):
        for key in ("PROXMOX_IP", "GATEWAY", "ADGUARD_IP", "CADDY_IP", "COMPANION_IP", "NDI_IP"):
            self.env[key] = self.env[key].replace("192.168.1.", "10.23.0.")
        self.env["BASE_DOMAIN"] = ".estudio.lan"
        self.cfg = settings(self.env)
        services = self.load()
        generator = load_module("caddy_generator", ROOT / "cts/caddy/generate-caddy.py")
        caddy = generator.render(self.cfg, services) + generator.routes(self.cfg, services)
        self.assertIn('redir "https://10.23.0.10:8006{uri}" 302', caddy)
        self.assertIn("http://inicio.estudio.lan", caddy)
        self.assertNotIn("ndi.estudio.lan", caddy)
        self.assertNotIn("reverse_proxy", caddy)
        rewrites = load_module("adguard_generator", ROOT / "cts/adguard/generate-rewrites.py").render(self.cfg, services)
        self.assertEqual(rewrites["rewrites"], [
            {"domain": "*.estudio.lan", "answer": "10.23.0.12", "enabled": True},
            {"domain": "ndi.estudio.lan", "answer": "10.23.0.16", "enabled": True},
        ])

    def test_untrusted_text_is_escaped_and_dns_card_has_no_http_link(self):
        self.data[0]["name"] = '<script>alert("x")</script>'
        self.data[0]["description"] = '<img src=x onerror=alert(1)>'
        self.data[0]["category"] = '<b>Equipos</b>'
        services = self.load()
        html = load_module("landing_generator", ROOT / "cts/caddy/generate-landing.py").render(self.cfg, services)
        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)
        self.assertIn("&lt;b&gt;Equipos&lt;/b&gt;", html)
        self.assertIn("ndi.lan</code>", html)
        self.assertNotIn('href="http://ndi.lan', html)
        self.assertNotIn('src="https://', html)

    def test_config_rejects_collisions_and_invalid_network_values(self):
        for key, value in (("ADGUARD_CT_ID", "111"), ("CADDY_IP", "192.168.1.11"),
                           ("CADDY_IP", "192.168.2.12"), ("GATEWAY", "192.168.1.255"),
                           ("CIDR", "24;rm"), ("BRIDGE", "vmbr0,tag=2"),
                           ("BOOTSTRAP_DNS", "192.168.1.11"), ("UPSTREAM_DNS", "192.168.1.11"),
                           ("BOOTSTRAP_DNS", "192.168.1.12"), ("BOOTSTRAP_DNS", "192.168.1.15"),
                           ("BOOTSTRAP_DNS", "192.168.1.16"), ("UPSTREAM_DNS", "192.168.1.15"),
                           ("UPSTREAM_DNS", "192.168.1.16"), ("UPSTREAM_DNS", "   "),
                           ("COMPANION_CT_ID", "110"), ("NDI_CT_ID", "112"),
                           ("COMPANION_IP", "192.168.1.16"), ("NDI_IP", "192.168.2.16"),
                           ("COMPANION_PORT", "0"), ("NDI_PORT", "65536"),
                           ("COMPANION_BUILD", "stable;echo"), ("NDI_INSTALLER_SHA256", "bad")):
            with self.subTest(key=key, value=value):
                env = dict(self.env, **{key: value})
                with self.assertRaises(ConfigError):
                    settings(env)

    def test_bad_inventory_is_rejected_before_any_render(self):
        mutations = [
            ("subdomain", "inicio"), ("subdomain", "companion"),
            ("subdomain", "../etc"), ("mode", "proxy"),
            ("target", "javascript:alert(1)"),
            ("target", "http://example.com/\nfile_server"),
            ("target", 'http://example.com/"{env.SECRET}'),
            ("target", "http://${UNKNOWN_IP}"),
            ("target", "http://admin:password@example.com"),
            ("target", "http://example.com?x=1"),
            ("target", "http://proxmox.lan"),
            ("target", "http://${CADDY_IP}"),
            ("target", "http://example.com:99999"),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                data = copy.deepcopy(self.data)
                data[0][key] = value
                with self.assertRaises(ConfigError):
                    self.load(data)

    def test_inventory_shape_and_dns_address_validation(self):
        for data in ({}, [], [1], [{"name": "equipo"}]):
            with self.subTest(data=data), self.assertRaises(ConfigError):
                self.load(data)
        for ip in ("", "bad", "127.0.0.1", "224.0.0.1"):
            data = copy.deepcopy(self.data)
            data[-1]["ip"] = ip
            with self.subTest(ip=ip), self.assertRaises(ConfigError):
                self.load(data)

    def test_output_is_deterministic(self):
        services = self.load()
        for filename in ("generate-caddy.py", "generate-landing.py"):
            module = load_module(filename, ROOT / "cts/caddy" / filename)
            self.assertEqual(module.render(self.cfg, services), module.render(self.cfg, services))

    def test_render_cli_scopes_output_to_selected_ct(self):
        expected = {
            "adguard": {"adguard/rewrites.json"},
            "caddy": {"caddy/Caddyfile", "caddy/index.html", "caddy/routes.caddy", "caddy/seed.json"},
            "companion": {"companion/companion-override.conf", "companion/apply.json"},
            "ndi": {"ndi/ndi-discovery.service", "ndi/ndi-discovery-service.v1.json", "ndi/apply.json"},
        }
        expected["all"] = set().union(*expected.values())
        for ct, files in expected.items():
            with self.subTest(ct=ct):
                output = Path(self.tmp.name) / ct
                result = subprocess.run([sys.executable, str(ROOT / "scripts/render.py"),
                    "--services", str(ROOT / "services.json"), "--ct", ct,
                    "--output-dir", str(output)], env={**os.environ, **self.env}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
                self.assertEqual(actual, files)

    def test_application_ports_match_landing_service_config_and_health_checks(self):
        self.env.update(COMPANION_PORT="8088", NDI_PORT="5960")
        cfg = settings(self.env)
        services = load_services(ROOT / "services.json", cfg)
        companion = load_module("companion_generator", ROOT / "cts/companion/generate-config.py").render(cfg, services)
        self.assertIn("--admin-port 8088", companion["companion-override.conf"])
        self.assertIn("--admin-address 0.0.0.0", companion["companion-override.conf"])
        self.assertEqual(json.loads(companion["apply.json"])["health"]["port"], 8088)
        self.assertEqual(next(s for s in services if s["subdomain"] == "companion")["target"], "http://192.168.1.15:8088")
        ndi = load_module("ndi_generator", ROOT / "cts/ndi/generate-config.py").render(cfg, services)
        self.assertEqual(json.loads(ndi["ndi-discovery-service.v1.json"]), {"binding": "192.168.1.16", "port_no": "5960"})
        self.assertEqual(json.loads(ndi["apply.json"])["health"], {"kind": "tcp", "host": "192.168.1.16", "port": 5960})
        self.assertIn("-service -config /etc/ndi/", ndi["ndi-discovery.service"])

    def test_config_and_native_services_do_not_require_seed_inventory(self):
        missing = Path(self.tmp.name) / "missing.json"
        for args in (("--config-only",), ("--ct", "companion"), ("--ct", "ndi")):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, str(ROOT / "scripts/render.py"),
                    "--services", str(missing), *args], env={**os.environ, **self.env}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_inventory_can_be_empty_but_must_be_valid(self):
        self.path.write_text("[]", encoding="utf-8")
        self.assertEqual(load_services(self.path, self.cfg, allow_empty=True), [])
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_services(self.path, self.cfg, allow_empty=True)

    def test_questionnaire_generates_selected_cts_with_independent_nics(self):
        wizard = load_module("questionnaire", ROOT / "scripts/questionnaire.py")
        destination = Path(self.tmp.name) / "config.env"
        answers = iter([
            "s", "n", "s", "n",  # Selección.
            "", "", "", "", "",  # Almacenamientos, DNS y dominio.
            "", "", "1", "", "10.20.0.11/24", "20", "10.20.0.1",
            "", "", "2", "", "10.20.0.15/24", "20", "10.20.0.1", "vmbr1", "172.20.0.15/24", "30",
        ])
        with patch.object(wizard, "host_network", return_value=({
                "192.168.1.99": ("vmbr0", 24), "192.168.1.10": ("vmbr0", 24)},
                ["vmbr0", "vmbr1"], {"dev": "vmbr0", "gateway": "192.168.1.1", "prefsrc": "192.168.1.10"})), \
                patch("builtins.input", side_effect=lambda _: next(answers)), patch("builtins.print"):
            wizard.questionnaire(destination, CT_ROLES, existing=False)
        persisted = dict(shlex.split(line)[0].split("=", 1)
                         for line in destination.read_text(encoding="utf-8").splitlines() if not line.startswith("#"))
        cfg = settings(persisted)
        self.assertEqual(cfg["PROXMOX_IP"], "192.168.1.10")
        self.assertEqual(cfg["INSTALL_CTS"], "adguard companion")
        self.assertEqual(cfg["CADDY_IP"], "")
        self.assertEqual(cfg["NDI_IP"], "")
        nics = role_networks(cfg, "companion")
        self.assertEqual(nics[1], {"bridge": "vmbr1", "ip": "172.20.0.15/24", "gateway": "", "vlan": "30"})
        self.assertEqual(cfg["COMPANION_IP"], "10.20.0.15")
        services = load_services(ROOT / "services.json", cfg)
        self.assertEqual([service["subdomain"] for service in services], ["proxmox", "companion", "adguard"])
        output = Path(self.tmp.name) / "rendered"
        result = subprocess.run([sys.executable, str(ROOT / "scripts/render.py"),
                                 "--services", str(ROOT / "services.json"), "--output-dir", str(output)],
                                env={**os.environ, **persisted}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({path.name for path in output.iterdir()}, {"adguard", "companion"})
        self.assertEqual(json.loads((output / "adguard/rewrites.json").read_text(encoding="utf-8")), {"rewrites": []})

    def test_questionnaire_asks_for_missing_prefix_without_repeating_accepted_fields(self):
        wizard = load_module("questionnaire", ROOT / "scripts/questionnaire.py")
        answers = ["", "", "172.19.200.1", "31", "17", "4095", "-", "172.19.1.1", "172.19.160.1"]
        with patch("builtins.input", side_effect=answers) as prompt, patch("builtins.print"):
            nics = wizard.ask_nics("adguard", [], "vmbr0", {"dev": "vmbr0", "gateway": "172.19.160.1"}, ["vmbr0"])
        self.assertEqual(nics, [{"bridge": "vmbr0", "ip": "172.19.200.1/17", "vlan": "", "gateway": "172.19.160.1"}])
        labels = [call.args[0] for call in prompt.call_args_list]
        self.assertEqual([sum(field in label for label in labels) for field in
                          ("puente", "IPv4 o IPv4/CIDR", "prefijo CIDR", "VLAN", "puerta de enlace")],
                         [1, 1, 2, 2, 2])

    def test_nics_reject_duplicate_addresses_extra_gateways_and_invalid_vlans(self):
        primary = {"bridge": "vmbr0", "ip": "192.168.1.15/24", "vlan": "20", "gateway": "192.168.1.1"}
        secondary = {"bridge": "vmbr2", "ip": "10.20.0.15/24", "vlan": "", "gateway": ""}
        for patch_nic in ({"ip": "192.168.1.11/24"}, {"gateway": "10.20.0.1"}, {"vlan": "4095"},
                          {"bridge": "vmbr2,tag=5"}, {"ip": "10.20.0.0/24"}):
            with self.subTest(nic=patch_nic), self.assertRaises(ConfigError):
                settings(dict(self.env, COMPANION_NICS=json.dumps([primary, {**secondary, **patch_nic}])))


if __name__ == "__main__":
    unittest.main()
