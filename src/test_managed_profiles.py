"""Integration checks against supplied original assemblies; never runs the app."""
from __future__ import annotations
import argparse
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def run(args: list[str | Path], expected: int = 0) -> subprocess.CompletedProcess:
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True)
    if result.returncode != expected:
        raise AssertionError(f"Unexpected exit {result.returncode}: {result.stdout}\n{result.stderr}")
    return result

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--originals', required=True, type=Path)
    parser.add_argument('--pairing', required=True, type=Path)
    parser.add_argument('--certificate', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    pairing = json.loads(args.pairing.read_text(encoding='utf-8-sig'))
    tool = ROOT / 'tools/PatchManaged.exe'
    fixture = ROOT / 'tools/ManagedProfileFixtures.exe'
    results = {}
    baseline = json.loads(run([tool, '--probe', args.originals]).stdout)
    results['baseline_accepted'] = baseline['compatible']
    with tempfile.TemporaryDirectory(prefix='vd-managed-profiles-') as temporary:
        temporary = Path(temporary)
        for mode in ('metadata', 'layout', 'encoding', 'bad-constant', 'bad-control'):
            source = temporary / mode
            run([fixture, args.originals, source, mode])
            output = temporary / (mode + '-patched')
            if mode.startswith('bad-'):
                result = run([tool, source, output, pairing['account'], pairing['signature_base64'], args.certificate], 1)
                assert 'behavior differs from profile' in result.stderr
                assert not output.exists(), 'Incompatible input wrote output before completing preflight'
                results[mode + '_rejected_before_writes'] = True
            else:
                probe = json.loads(run([tool, '--probe', source]).stdout)
                assert probe['compatible']
                assert probe['source_sha256'] != baseline['source_sha256'], 'Fixture failed to alter input bytes'
                report = json.loads(run([tool, source, output, pairing['account'], pairing['signature_base64'], args.certificate]).stdout)
                assert len(report['outputs']) == 2
                assert all(x['definition_tokens_preserved'] and x['reference_tokens_preserved'] for x in report['outputs'])
                if mode == 'metadata':
                    assert report['version'] == '1.34.26.0'
                results[mode + '_change_accepted_and_patched'] = True
                if mode == 'layout':
                    original_tokens = {x['id']: x['token'] for x in baseline['methods']}
                    changed_tokens = {x['id']: x['token'] for x in probe['methods']}
                    assert original_tokens != changed_tokens, 'Layout fixture did not change any method tokens'
                    results['changed_metadata_tokens_accepted'] = True
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'profile_id': baseline['profile_id'], 'required_methods': len(baseline['methods']),
                                      'tests': results, 'runtime_executed': False}, indent=2) + '\n')
    print(json.dumps(results, indent=2))

if __name__ == '__main__':
    main()
