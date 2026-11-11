"""Build reproducible file sets for the Windows distribution and source release."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from farmwatch import __version__
from farmwatch.vision.controls import required_assets


def payload(*, source=False):
    files = {name: ROOT/name for name in (
        'README.md', 'CHANGELOG.md', 'LICENSE', 'requirements.txt', 'settings.example.ini',
        'start.bat', 'start_background.bat', 'stop_background.bat',
        'scripts/bootstrap.bat', 'resources/branding/brandmark.svg',
        'resources/routes/pinned-friends.json',
        'resources/ui/1280x720/capture-manifest.json',
    )}
    # Source checkouts generate their own private configuration on first start.
    if not source:
        files['settings.ini'] = ROOT/'settings.example.ini'
    for path in sorted((ROOT/'farmwatch').rglob('*.py')):
        files[path.relative_to(ROOT).as_posix()] = path
    for name in required_assets():
        relative=f'resources/ui/1280x720/{name}'
        files[relative]=ROOT/relative
    for name in ('01_install.bat','02_check_mumu.bat','03_start.bat','00_先看我_安装说明.txt'):
        relative=f'scripts/share_windows/{name}' if source else name
        files[relative]=ROOT/'scripts/share_windows'/name
    if source:
        for name in ('pyproject.toml','.gitignore','.gitattributes','requirements-dev.txt',
                     'scripts/package.py','scripts/verify_release.py','.github/workflows/ci.yml'):
            files[name]=ROOT/name
        for pattern in ('*.py','*.png','*.json','*.md'):
            for path in sorted((ROOT/'tests').rglob(pattern)):
                files[path.relative_to(ROOT).as_posix()]=path
    content={}
    for relative, path in sorted(files.items()):
        resolved=path.resolve(strict=True)
        if not resolved.is_relative_to(ROOT) or not resolved.is_file():
            raise ValueError(f'Invalid release file: {relative}')
        data=resolved.read_bytes()
        if path.suffix in {'.bat','.ini','.txt'}:
            data=data.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
        content['FarmWatch/'+relative]=data
    manifest={'product':'FarmWatch','version':__version__,
              'edition':'source' if source else 'windows','resolution':[1280,720],
              'built_at':datetime.now(timezone.utc).isoformat(),
              'files':{name:hashlib.sha256(data).hexdigest() for name,data in content.items()}}
    content['FarmWatch/release-info.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')
    return content


def build(*, source=False):
    content=payload(source=source)
    destination=ROOT/'dist'/('FarmWatch-Source.zip' if source else 'FarmWatch-Windows.zip')
    destination.parent.mkdir(exist_ok=True)
    pending=destination.with_suffix('.zip.partial')
    with ZipFile(pending,'w',ZIP_DEFLATED,compresslevel=9) as archive:
        for name,data in content.items():
            archive.writestr(name,data)
    with ZipFile(pending) as archive:
        if set(archive.namelist()) != set(content) or archive.testzip() is not None:
            raise RuntimeError('Release archive validation failed')
    pending.replace(destination)
    print(f'{destination.name}: {len(content)} files, {destination.stat().st_size:,} bytes')
    return destination


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='Build a FarmWatch release')
    parser.add_argument('--source',action='store_true',help='Include tests and development files')
    build(source=parser.parse_args().source)
