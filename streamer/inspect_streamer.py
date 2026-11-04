from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = Path(__file__).resolve().parent/'evidence'
ILSPY = ROOT / 'tools/ilspycmd/tools/net8.0/any/ilspycmd.dll'
TARGET = Path(r'C:\Program Files\Virtual Desktop Streamer\VirtualDesktop.Streamer.exe')
TYPES = {
    'hash-delegate': '\x9a\x08',
    'padding-delegate': '\x9b\x08',
    'verify-delegate': '\x9c\x08',
    'delegate-resolver': '\x84.\x03',
    'platform-account': 'VirtualDesktop.Interfaces.PlatformAccountCollection',
    'account-helper': '\x84.\x04',
    'update-helper': 'VirtualDesktop.Streamer.UpdateHelper',
}
for label, name in TYPES.items():
    output = PYTHON_ROOT / (label + ('.il' if label.endswith('-il') else '.cs'))
    if output.exists():
        continue
    args = ['dotnet', str(ILSPY), '--disable-updatecheck', '-t', name]
    if label.endswith('-il'):
        args.append('-il')
    args.append(str(TARGET))
    with output.open('wb') as stream:
        result = subprocess.run(args, stdout=stream, stderr=subprocess.PIPE)
    print(label, result.returncode, output.stat().st_size, result.stderr.decode(errors='replace')[:300])
