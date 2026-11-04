"""Generate a paired Streamer executable and signed Quest APK using checked code profiles.

Build only: this tool never installs, launches, stops, or uninstalls Virtual Desktop.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APK_HASH = '3b6ff43614e4af1c4aa70f6ff3e5ad3108c7f29fb8f1657a2a134e104ee050b9'
STREAMER_HASH = '0229a322137f55687240d2b51f5f7002056d9277d7fa001ec6cb4eb25af0bfb8'
VERIFIER_HASH = '9a493a54d19c990ac74d0055f6a4679ca7f0c9e25dda0606fbe03c49ac3cda14'

def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else _hash_stream(stream)

def _hash_stream(stream):
    h = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        h.update(block)
    return h.hexdigest()

def module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result

def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)

def run(stage, arguments, logs):
    print(stage + '...', flush=True)
    log = logs / (stage.replace(' ', '-').lower() + '.txt')
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run([str(x) for x in arguments], stdout=stream, stderr=subprocess.STDOUT,
                                cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f'{stage} failed ({result.returncode}). See {log}\n' +
                           log.read_text(encoding='utf-8', errors='replace')[-6000:])
    return log

def ensure_verifier():
    tool = ROOT / 'tools/ilverify/tools/net9.0/any/ILVerify.dll'
    archive = ROOT / 'tools/ilverify.zip'
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        print('Downloading Microsoft ILVerify 9.0.0...', flush=True)
        url = 'https://api.nuget.org/v3-flatcontainer/dotnet-ilverify/9.0.0/dotnet-ilverify.9.0.0.nupkg'
        with urllib.request.urlopen(url, timeout=120) as src, archive.open('xb') as dest:
            shutil.copyfileobj(src, dest)
    if digest(archive) != VERIFIER_HASH:
        raise ValueError('ILVerify download SHA-256 mismatch')
    with zipfile.ZipFile(archive) as z:
        destination = ROOT / 'tools/ilverify'
        if not tool.is_file():
            z.extractall(destination)
        for info in z.infolist():
            if info.is_dir():
                continue
            cached = destination / info.filename
            if not cached.is_file() or digest(cached) != hashlib.sha256(z.read(info)).hexdigest():
                raise ValueError('Cached ILVerify tool differs from pinned package: ' + info.filename)
    return tool

def read_apk_metadata(apk, logs):
    log = run('Read APK manifest', [ROOT / 'android-runtime/tools/android/aapt2.exe',
                                  'dump', 'badging', apk], logs)
    text = log.read_text(encoding='utf-8', errors='replace')
    match = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", text, re.MULTILINE)
    if not match or match[1] != 'VirtualDesktop.Android':
        raise ValueError('The APK manifest must identify the VirtualDesktop.Android package')
    return {'package': match[1], 'version_code': int(match[2]), 'version': match[3]}

def versions_match(apk_version, pc_version):
    # File versions sometimes append a zero fourth component to a three-part
    # Android versionName. No other version mismatch is inferred compatible.
    def parse(value):
        if not re.fullmatch(r'\d+(?:\.\d+){2,3}', str(value)):
            raise ValueError('Unsupported application version syntax: ' + str(value))
        parts = tuple(map(int, value.split('.')))
        return parts + (0,) * (4 - len(parts))
    return parse(apk_version) == parse(pc_version)

def managed_tool_current():
    # Source hashes survive ZIP extraction. Timestamps do not, and must not
    # cause the single-EXE tool to recompile files inside its verified cache.
    tool = ROOT / 'tools/PatchManaged.exe'
    receipt = ROOT / 'tools/PatchManaged.build.json'
    if not tool.is_file() or not receipt.is_file():
        return False
    try:
        record = json.loads(receipt.read_text(encoding='utf-8-sig'))
        sources = ('src/PatchManaged.cs', 'src/ManagedProfile.cs', 'Build-ManagedTool.ps1')
        return (record.get('schema_version') == 1 and
                record.get('executable_sha256') == digest(tool) and
                record.get('source_sha256') == {name: digest(ROOT / name) for name in sources})
    except (ValueError, OSError):
        return False

def build(args):
    if os.name != 'nt':
        raise RuntimeError('This build wrapper currently supports Windows only')
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11 or newer is required')
    apk = args.apk.resolve(strict=True)
    streamer = args.streamer.resolve(strict=True)
    source_apk_hash, source_pc_hash = digest(apk), digest(streamer)
    out = args.output.resolve()
    if out.exists():
        raise FileExistsError('Output directory already exists. Choose a new output; previous builds are retained.')
    bundled_dotnet = ROOT / 'runtime/dotnet/dotnet.exe'
    dotnet = str(bundled_dotnet) if bundled_dotnet.is_file() else shutil.which('dotnet')
    if not dotnet:
        raise RuntimeError('A .NET 9 or later runtime is required for IL verification (no SDK needed).')
    out.mkdir(parents=True)
    logs = out / 'verification'
    logs.mkdir()
    incomplete = out / 'BUILD-INCOMPLETE.txt'
    incomplete.write_text('Do not install artifacts from this directory until build-report.json exists.\n')
    work = out / 'work'
    work.mkdir()
    tool = ROOT / 'tools/PatchManaged.exe'
    if not managed_tool_current():
        run('Build managed patch tool', ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', ROOT / 'Build-ManagedTool.ps1'], logs)
    stream_patch = module('vd_streamer_patch', ROOT / 'streamer/patch_streamer.py')
    print('Validate Streamer code profile...', flush=True)
    pc_compatibility = stream_patch.inspect_streamer(streamer)
    write_json(logs / 'streamer-compatibility.json', pc_compatibility)
    signing = module('vd_signing', ROOT / 'android-runtime/signing.py')
    try:
        signing.tool_paths()
        if not (ROOT / 'android-runtime/tools/android/aapt2.exe').is_file():
            raise RuntimeError('APK manifest inspector missing')
    except RuntimeError:
        run('Prepare Android tools', [sys.executable, ROOT / 'android-runtime/prepare_signing_tools.py'], logs)
    apk_metadata = read_apk_metadata(apk, logs)
    pc_version = pc_compatibility['version']
    if not versions_match(apk_metadata['version'], pc_version):
        raise ValueError('Provide matching APK and Streamer versions; cross-version protocol compatibility is not established')
    original_managed, modified_managed = work / 'original-managed', work / 'modified-managed'
    run('Extract original assemblies', [sys.executable, ROOT / 'android-runtime/repack_apk.py',
        'extract', '--apk', apk, '--output', original_managed, '--expected-sha256', source_apk_hash], logs)
    probe_log = run('Validate APK code profile', [tool, '--probe', original_managed], logs)
    apk_compatibility = json.loads(probe_log.read_text(encoding='utf-8-sig'))
    if apk_compatibility.get('compatible') is not True:
        raise ValueError('The APK code profile did not report a complete match')
    pair_path = args.pairing.resolve()
    if not pair_path.exists():
        pair_path.parent.mkdir(parents=True, exist_ok=True)
        run('Generate pairing', ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            ROOT / 'streamer/New-Pairing.ps1', '-Output', pair_path], logs)
    pair = json.loads(pair_path.read_text(encoding='utf-8-sig'))
    stream_patch.validate_pairing(pair)
    if pair_path != out / 'pairing.json':
        shutil.copyfile(pair_path, out / 'pairing.json')
    key_dir = args.signing_directory.resolve()
    run('Validate signing identity', [sys.executable, ROOT / 'android-runtime/signing.py',
        'init', '--directory', key_dir], logs)
    cert = key_dir / 'apk-signing.der'
    if cert != out / 'apk-signing.der':
        shutil.copyfile(cert, out / 'apk-signing.der')
    verifier = ensure_verifier()
    run('Patch managed assemblies', [tool, original_managed, modified_managed,
        pair['account'], pair['signature_base64'], cert], logs)
    verify_selections = {
        'VirtualDesktop.Android.dll': ('VrApp.CreateAccessTokenAsync|VrApp.GetAccessTokenAsync|VrApp.OnCreate', 3),
        'VirtualDesktop.Mobile.dll': ('UserSettings.get_HasValidIdentity|UserSettings.get_Signature|UserSettings.*GetHasValidIdentityAsync|InputSystem..ctor|Keyboard..ctor|UIGameSystem.LoadPage|BottomPanel..ctor', 8)
    }
    il_results = []
    for dll, (selection, minimum) in verify_selections.items():
        log = run('Verify IL ' + dll, [dotnet, '--roll-forward', 'Major', verifier,
            modified_managed / dll, '-r', str(original_managed / '*.dll'), '-s', 'System.Private.CoreLib',
            '-i', selection, '--statistics'], logs)
        text = log.read_text(encoding='utf-8')
        match = re.search(r'Methods verified:\s*(\d+)', text)
        if not match or int(match[1]) < minimum:
            raise RuntimeError('IL verification selected too few methods; inspect ' + str(log))
        il_results.append({'assembly': dll, 'methods_verified': int(match[1]), 'passed': True})
    patched_pc = out / 'VirtualDesktop.Streamer.exe'
    print('Generate paired Streamer...', flush=True)
    pc_report = stream_patch.patch_streamer(streamer, patched_pc, pair['modulus_base64'])
    unsigned = work / 'VirtualDesktop.unsigned.apk'
    signed = out / 'VirtualDesktop-local.apk'
    run('Repack APK', [sys.executable, ROOT / 'android-runtime/repack_apk.py', 'repack',
        '--apk', apk, '--assemblies', modified_managed, '--output', unsigned,
        '--expected-sha256', source_apk_hash], logs)
    run('Sign and verify APK', [sys.executable, ROOT / 'android-runtime/signing.py', 'sign',
        '--apk', unsigned, '--output', signed, '--directory', key_dir], logs)
    audit_file = logs / 'apk-final-audit.json'
    run('Audit final APK', [sys.executable, ROOT / 'android-runtime/audit_apk.py',
        '--original', apk, '--patched', signed, '--assemblies', modified_managed,
        '--certificate', cert, '--report', audit_file, '--expected-original-sha256', source_apk_hash], logs)
    # Check apksigner verified the same certificate which the managed guards pin.
    signing_log = (logs / 'sign-and-verify-apk.txt').read_text(encoding='utf-8')
    cert_hash = digest(cert)
    if cert_hash not in signing_log.lower().replace(':', ''):
        raise RuntimeError('APK verifier did not report the expected signing certificate')
    report = {
        'schema_version': 1, 'build_utc': datetime.now(timezone.utc).isoformat(),
        'version': pc_version, 'apk_version_code': apk_metadata['version_code'], 'account': pair['account'],
        'inputs': {'apk': {'path': str(apk), 'sha256': source_apk_hash, **apk_metadata},
                   'streamer': {'path': str(streamer), 'sha256': source_pc_hash, 'version': pc_version}},
        'compatibility': {'matched': True, 'streamer': pc_compatibility, 'apk': apk_compatibility,
                          'baseline_version': '1.34.25.0', 'cross_version_runtime_tested': False},
        'outputs': {signed.name: digest(signed), patched_pc.name: digest(patched_pc),
                    'pairing.json': digest(out / 'pairing.json'), 'apk-signing.der': cert_hash},
        'pairing_signature_verified': True, 'managed_il': il_results,
        'managed_patch': json.loads((modified_managed / 'managed-patch-report.json').read_text()),
        'streamer_patch': pc_report,
        'apk_repack': json.loads(unsigned.with_suffix('.apk.json').read_text()),
        'apk_signature_verified': True, 'native_alignment_verified': 16384,
        'apk_final_audit': json.loads(audit_file.read_text()),
        'runtime_tested': False, 'installed': False,
        'limitations': ['VR startup, video/audio/tracking, restart and reconnect require device testing.',
            'APK keeps the original package name but has a new signing certificate; it cannot update the stock signed APK.',
            'Streamer publisher signature becomes invalid after the local modifications.',
            'Local pairing is a signed bearer identity; keep the APK and pairing.json private.']
    }
    if digest(apk) != source_apk_hash or digest(streamer) != source_pc_hash:
        raise ValueError('An original input changed during the build; generated files are incomplete')
    write_json(out / 'build-report.json', report)
    incomplete.unlink()
    print(json.dumps({'output': str(out), 'account': pair['account'], 'apk': str(signed),
                      'streamer': str(patched_pc), 'runtime_tested': False, 'installed': False}, indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk', type=Path, required=True)
    parser.add_argument('--streamer', type=Path, default=Path(r'C:\Program Files\Virtual Desktop Streamer\VirtualDesktop.Streamer.exe'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pairing', type=Path, default=ROOT / 'local/pairing.json')
    parser.add_argument('--signing-directory', type=Path, default=ROOT / 'android-runtime/keys')
    try:
        build(parser.parse_args())
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
