"""Build a read-only update report from verified CTs and vendor metadata."""

from datetime import datetime, timezone
from http.client import HTTPException
import json
import re
import subprocess
import sys
import os
import tarfile
from urllib.request import Request, urlopen

from ndi_release import CACHE, fetch_release
from ndi_sdk import display_license, read_license
from panel_release import validate_release

GITHUB_API = 'https://api.github.com/repos/'
RELEASE_REPOS = {'adguard': 'AdguardTeam/AdGuardHome', 'caddy': 'caddyserver/caddy'}
COMPANION_API = 'https://api.bitfocus.io/v1/product/companion/packages?branch=stable&limit=1&target=linux-tgz'
COMMIT = re.compile(r'[a-f0-9]{40}')
VERSION = re.compile(r'(?<![\w.])v?(\d+)\.(\d+)\.(\d+)(-[\w.-]+)?', re.IGNORECASE)


def get_json(url, media_type='application/json'):
    request = Request(url, headers={'User-Agent': 'Fluge-Control-Updates', 'Accept': media_type})
    with urlopen(request, timeout=7) as response:
        document = json.loads(response.read(2 * 1024 * 1024))
    if not isinstance(document, dict):
        raise ValueError('Invalid response')
    return document


def github_origin(checkout):
    result = subprocess.run(['git', '-C', checkout, 'remote', 'get-url', 'origin'],
                            capture_output=True, text=True, check=True, timeout=5)
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)'
                         r'([\w.-]+/[\w.-]+?)(?:\.git)?/?', result.stdout.strip(), re.ASCII)
    return match[1] if match else None


def compare_panel(checkout, revision):
    commit = revision.get('commit')
    commit = commit if isinstance(commit, str) and COMMIT.fullmatch(commit) else None
    dirty = revision.get('dirty') is True
    entry = {'installed': commit, 'latest': None, 'dirty': dirty, 'status': 'unknown_remote'}
    try:
        entry['installedRelease'] = validate_release(revision.get('release'))
    except ValueError:
        # A legacy CT records its commit and gains version metadata on its next deployment.
        entry['installedRelease'] = None
    try:
        repo = github_origin(checkout)
        if not repo:
            return entry
        latest = get_json(f'{GITHUB_API}{repo}/commits/HEAD').get('sha')
        if not isinstance(latest, str) or not COMMIT.fullmatch(latest):
            return entry
        release = validate_release(get_json(f'{GITHUB_API}{repo}/contents/panel-version.json?ref={latest}',
                                            'application/vnd.github.raw+json'))
        if not release['update_required']:
            entry.update(latestRelease=entry['installedRelease'],
                         status='local_changes' if dirty else 'current' if entry['installedRelease'] else 'unknown_local')
            return entry
        entry['latest'] = latest
        entry['latestRelease'] = release
        if not commit or dirty:
            entry['status'] = 'local_changes' if dirty else 'unknown_local'
        elif commit == latest:
            entry['status'] = 'current'
        else:
            compare = f'{commit}...{latest}'
            status = get_json(f'{GITHUB_API}{repo}/compare/{compare}?per_page=1').get('status')
            entry['status'] = {'ahead': 'update', 'behind': 'newer', 'diverged': 'diverged'}.get(status, 'unknown_remote')
    except (OSError, HTTPException, ValueError, TypeError, subprocess.SubprocessError) as error:
        print(f'Panel Fluge: {error}', file=sys.stderr)
    return entry


def stable_version(role):
    if role == 'companion':
        packages = get_json(COMPANION_API).get('packages', [])
        return next((p.get('version') for p in packages if isinstance(p, dict) and p.get('target') == 'linux-tgz'), None)
    return get_json(f'{GITHUB_API}{RELEASE_REPOS[role]}/releases/latest').get('tag_name')


def compare_product(role, state, raw):
    entry = {'installed': None, 'latest': None, 'status': state}
    if state != 'available':
        return entry
    if role == 'ndi':
        sdk = re.search(r'libndi\.so\.(\d+\.\d+\.\d+)$', raw)
        entry.update(installed=f'SDK {sdk[1]}' if sdk else ('Instalado' if raw else None),
                     status='manual' if raw else 'unknown_local')
        return entry
    local = VERSION.search(raw)
    if not local:
        entry['status'] = 'unknown_local'
        return entry
    entry.update(installed=local[0].lstrip('vV'), status='unknown_remote')
    try:
        published = stable_version(role)
        latest = VERSION.fullmatch(published or '')
        if latest and not latest[4]:
            local_order = tuple(map(int, local.groups()[:3])) + (not local[4],)
            stable_order = tuple(map(int, latest.groups()[:3])) + (True,)
            status = 'update' if local_order < stable_order else 'newer' if local_order > stable_order else 'current'
            entry.update(latest=latest[0].lstrip('vV'), status=status)
    except (OSError, HTTPException, ValueError, TypeError) as error:
        print(f'{role}: {error}', file=sys.stderr)
    return entry


def main():
    _, checkout, revision, *versions = sys.argv
    try:
        revision = json.loads(revision)
    except ValueError:
        revision = {}
    services = {'panel': compare_panel(checkout, revision if isinstance(revision, dict) else {})}
    for role, state, raw in zip(versions[::3], versions[1::3], versions[2::3], strict=True):
        services[role] = compare_product(role, state, raw)
        if role == 'ndi' and state == 'available':
            apply_ndi_release(services[role])
    for entry in services.values():
        entry['canUpdate'] = entry['status'] == 'update'
    json.dump({'checkedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'services': services}, sys.stdout)


def accepted_ndi_license():
    try:
        return (CACHE / 'accepted-license').read_text(encoding='ascii').strip()
    except FileNotFoundError:
        return os.environ.get('NDI_LICENSE_SHA256', '')


def apply_ndi_release(entry):
    try:
        latest = fetch_release()
        entry['latest'] = 'SDK ' + latest['version']
        installed_version = VERSION.search(entry['installed'] or '')
        if not installed_version:
            entry['status'] = 'unknown_local'
            return
        current_version = tuple(map(int, installed_version.groups()[:3]))
        published_version = tuple(map(int, latest['version'].split('.')))
        entry['status'] = 'update' if published_version > current_version else 'current' if published_version == current_version else 'newer'
        if entry['status'] == 'update' and latest['licenseHash'] != accepted_ndi_license():
            entry['license'] = {'hash': latest['licenseHash'], 'text': display_license(read_license(CACHE / 'installer.sh'))}
    except (OSError, HTTPException, ValueError, EOFError, tarfile.TarError) as error:
        entry['status'] = 'unknown_remote'
        print(f'NDI: {error}', file=sys.stderr)


if __name__ == '__main__':
    main()
