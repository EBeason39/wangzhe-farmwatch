"""Validate archives, isolated selftests, and rebuilding from the source release."""
from __future__ import annotations

import configparser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from package import build


def private_values():
    settings = configparser.ConfigParser(interpolation=None)
    settings.read(ROOT/'settings.ini', encoding='utf-8-sig')
    return [value.encode() for key in ('spt','sendkey')
            if (value := settings.get('notify',key,fallback=''))]


def inspect(path, *, source, secrets):
    with ZipFile(path) as archive:
        names = archive.namelist()
        if archive.testzip() is not None:
            raise RuntimeError(f'Invalid archive: {path.name}')
        forbidden = {'data','docs','logs','tmp','venv','runtime','.git','.agents','AGENTS.md','.env'}
        for name in names:
            parts = Path(name).parts
            if len(parts) < 2 or parts[0] != 'FarmWatch' or '..' in parts or parts[1] in forbidden:
                raise RuntimeError(f'Unexpected release path: {name}')
        manifest = json.loads(archive.read('FarmWatch/release-info.json'))
        if set(names) != set(manifest['files']) | {'FarmWatch/release-info.json'}:
            raise RuntimeError('Release manifest differs from archive contents')
        for name,digest in manifest['files'].items():
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != digest:
                raise RuntimeError(f'Hash mismatch: {name}')
            if any(secret in data for secret in secrets):
                raise RuntimeError(f'Private credential in release file: {name}')
        required_public = {'FarmWatch/LICENSE', 'FarmWatch/README.md',
                           'FarmWatch/resources/branding/brandmark.svg'}
        if not required_public.issubset(names):
            raise RuntimeError('Public usage files are missing')
        if source:
            required = {'FarmWatch/requirements-dev.txt', 'FarmWatch/scripts/package.py',
                        'FarmWatch/scripts/verify_release.py',
                        'FarmWatch/scripts/share_windows/01_install.bat',
                        'FarmWatch/tests/fixtures/friends/masking.json'}
            if not required.issubset(names) or 'FarmWatch/settings.ini' in names:
                raise RuntimeError('Source release is incomplete or contains private configuration')
        else:
            settings = configparser.ConfigParser(interpolation=None)
            settings.read_string(archive.read('FarmWatch/settings.ini').decode('utf-8-sig'))
            if settings.get('notify','spt') or settings.get('notify','sendkey'):
                raise RuntimeError('Windows configuration must have empty credentials')
            if any(name.startswith('FarmWatch/tests/') for name in names):
                raise RuntimeError('Test fixtures do not belong in the Windows runtime package')
        return manifest['files']


def run(command, cwd):
    environment = {key:value for key,value in os.environ.items() if not key.startswith('FARMWATCH_')}
    environment.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable,'-B','-X','utf8',*command],cwd=cwd,env=environment,
                            capture_output=True,text=True,encoding='utf-8',timeout=120)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)


def main():
    paths = {False:build(), True:build(source=True)}
    secrets = private_values()
    expected = {source:inspect(path,source=source,secrets=secrets) for source,path in paths.items()}
    for source,path in paths.items():
        with tempfile.TemporaryDirectory(prefix='farmwatch-release-') as temporary:
            with ZipFile(path) as archive:
                archive.extractall(temporary)
            checkout = Path(temporary)/'FarmWatch'
            run(['-m','farmwatch','selftest'],checkout)
            if source:
                for edition in (False,True):
                    run(['scripts/package.py'] + (['--source'] if edition else []),checkout)
                    rebuilt = checkout/'dist'/paths[edition].name
                    actual = inspect(rebuilt,source=edition,secrets=secrets)
                    if actual != expected[edition]:
                        raise RuntimeError('Rebuilding the source package changed release file contents')
        print(f'{path.name}: integrity, private-file exclusion and standalone selftest passed')
    print('Source archive rebuilt both editions with identical payload hashes.')


if __name__ == '__main__':
    main()
