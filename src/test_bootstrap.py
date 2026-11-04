"""Exercise extraction, reuse and corruption refusal without launching a GUI."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    payload = output / 'fixture.zip'
    records = []
    files = {name: ('Synthetic verification fixture: ' + name).encode() for name in (
        'toolchain_cli.py', 'tools/VdOfflineTool.exe', 'runtime/python/python.exe', 'runtime/dotnet/dotnet.exe')}
    for name, data in files.items():
        records.append({'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    manifest = {'schema_version': 1, 'file_count': len(records), 'portable': True,
                'application_binaries_included': False, 'private_identity_included': False, 'files': records}
    with zipfile.ZipFile(payload, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr('VirtualDesktop-OfflineTool/' + name, data)
        archive.writestr('VirtualDesktop-OfflineTool/delivery-manifest.json', json.dumps(manifest))
    executable = output / 'bootstrap-fixture.exe'
    subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'Build-Standalone.ps1'),
                    '-PayloadZip', str(payload), '-OutputExe', str(executable)], check=True)
    report1 = output / 'first-run.json'
    first = subprocess.run([str(executable), '--verify-only', '--report', str(report1)])
    one = json.loads(report1.read_text())
    assert first.returncode == 0 and one['success'] and not one['gui_launched']
    cache = Path(one['cache_directory'])
    report2 = output / 'second-run.json'
    second = subprocess.run([str(executable), '--verify-only', '--report', str(report2)])
    two = json.loads(report2.read_text())
    assert second.returncode == 0 and two['success'] and two['cache_reused'] and not two['gui_launched']
    # Change only the known synthetic fixture file in this payload's own cache.
    target = cache / 'tools/VdOfflineTool.exe'
    assert target.read_bytes() == files['tools/VdOfflineTool.exe']
    target.write_bytes(b'changed synthetic fixture')
    report3 = output / 'corrupt-cache.json'
    third = subprocess.run([str(executable), '--verify-only', '--report', str(report3)])
    three = json.loads(report3.read_text())
    assert third.returncode == 1 and not three['success'] and not three['gui_launched']
    assert target.read_bytes() == b'changed synthetic fixture', 'Corrupt cache was overwritten'
    # Restore the exact fixture bytes so this test leaves a valid harmless cache.
    target.write_bytes(files['tools/VdOfflineTool.exe'])
    final = {'fresh_extraction_passed': True, 'cache_reuse_passed': True,
             'corrupt_cache_refused_unchanged': True, 'gui_launched': False,
             'cache_directory': str(cache), 'fixture_only': True}
    (output / 'test-results.json').write_text(json.dumps(final, indent=2) + '\n')
    print(json.dumps(final, indent=2))

if __name__ == '__main__':
    main()
