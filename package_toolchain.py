"""Package the local tool, excluding application binaries and private build state."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent
ROOT_SOURCES = ['.gitignore', 'README.md', 'LICENSE', 'build.py', 'toolchain_cli.py',
                'Build-Patches.ps1', 'Build-ManagedTool.ps1', 'Build-Tool.ps1',
                'Launch-Tool.ps1', 'Prepare-Runtime.ps1', 'runtime-pins.json',
                'Start.cmd', 'package_toolchain.py', 'Build-Standalone.ps1', 'Build-Release.ps1']

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def source_files():
    files = {name: ROOT / name for name in ROOT_SOURCES}
    for path in (ROOT / '.github/workflows').glob('*.yml'):
        files[path.relative_to(ROOT).as_posix()] = path
    for folder in ('src', 'profiles', 'toolchain', 'streamer', 'android-runtime'):
        for path in (ROOT / folder).rglob('*'):
            relative = path.relative_to(ROOT)
            if any(part in ('tools', 'keys', 'evidence', 'extracted', '__pycache__') for part in relative.parts):
                continue
            if path.is_file() and path.suffix in ('.py', '.ps1', '.cs', '.txt', '.manifest'):
                files[relative.as_posix()] = path
            if path.is_file() and path.suffix == '.json' and (
                folder == 'profiles' or path.name in ('streamer-spec.json', 'test-pairing.json')):
                files[relative.as_posix()] = path
    for name, path in files.items():
        if not path.is_file():
            raise FileNotFoundError('Missing source: ' + name)
    return files

def runtime_files():
    files = {}
    for folder in ('runtime/python', 'runtime/dotnet', 'tools/dnlib', 'tools/ilverify',
                   'android-runtime/tools/java', 'android-runtime/tools/android',
                   'android-runtime/tools/platform-tools'):
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                files[path.relative_to(ROOT).as_posix()] = path
    for name in ('runtime/runtime-manifest.json', 'tools/dnlib.dll', 'tools/dnlib.zip',
                 'tools/PatchManaged.exe', 'tools/PatchManaged.build.json', 'tools/VdOfflineTool.exe', 'tools/ilverify.zip',
                 'android-runtime/tools/manifest.json'):
        path = ROOT / name
        if not path.is_file():
            raise FileNotFoundError('Missing portable dependency: ' + name)
        files[name] = path
    return files

def write_zip(path, files):
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, source in sorted(files.items()):
            archive.write(source, 'VirtualDesktop-OfflineTool/' + name)

def package(destination, *, portable=True):
    destination = destination.resolve()
    # This packager never overwrites an existing delivery or copies local keys.
    destination.mkdir(parents=True, exist_ok=False)
    marker = destination / 'DELIVERY-INCOMPLETE.txt'
    marker.write_text('Packaging incomplete. Do not use this delivery.\n')
    source = source_files()
    files = dict(source)
    if portable:
        files.update(runtime_files())
    records = []
    for name, original in sorted(files.items()):
        original.resolve(strict=True).relative_to(ROOT)
        target = (destination / name).resolve()
        target.relative_to(destination)
        if original.suffix.lower() in ('.apk', '.p12', '.password', '.der'):
            raise ValueError('Private/application artifact in selection: ' + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        expected = sha(original)
        if sha(target) != expected:
            raise IOError('Copy verification failed: ' + name)
        records.append({'path': name, 'size': target.stat().st_size, 'sha256': expected})
    manifest = {'schema_version': 1, 'file_count': len(records), 'portable': portable,
                'application_binaries_included': False, 'private_identity_included': False,
                'runtime_tested': False, 'files': records}
    manifest_path = destination / 'delivery-manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    source_zip = destination.parent / (destination.name + '-source.zip')
    write_zip(source_zip, source)
    marker.unlink()
    portable_zip = None
    if portable:
        portable_zip = destination.parent / (destination.name + '-portable.zip')
        packaged = {name: destination / name for name in files}
        packaged['delivery-manifest.json'] = manifest_path
        write_zip(portable_zip, packaged)
    print(json.dumps({'destination': str(destination), 'verified_files': len(records),
                      'source_zip': str(source_zip), 'portable_zip': str(portable_zip) if portable_zip else None}, indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--source-only', action='store_true')
    args = parser.parse_args()
    package(args.destination, portable=not args.source_only)
