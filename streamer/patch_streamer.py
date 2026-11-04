"""Contextual IL signature, token-preserving local-pairing patch for Streamer.

Only reads the supplied original and writes a NEW output. Does not install or run VD.
Python 3.10+, Windows PowerShell and the pinned dnlib metadata reader.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import struct
import subprocess
import tempfile
from pathlib import Path

ORIGINAL_SHA256 = '0229a322137f55687240d2b51f5f7002056d9277d7fa001ec6cb4eb25af0bfb8'
ORIGINAL_SIZE = 22614552
ORIGINAL_MODULUS = ('me0As0RFsds/LfBcxyaFpK4xsTHbUxoql7VxqRJlfNoce4WhwaycH19VovDUelXeelcFc7dfGCm8D8AG30TKa3V8ntni3v4PkTnw6KSXka76svoUtgCgNz9u2P3g17KuiWGN7hzJE2c84Pv25VgMhPhZfI/qv8JmYhU/pQ1XMc6t8Qa/CWLUqf9wIh/6Ey3wHFmwSpg9GhyPYDEUI+Ppn36Ia+2SjI4t764mY4OehVutBAsEOpFL9rlyGQggsrfOErp1xN6U5VM1Bibzqftjnjs5hy6bi/G5GS8lvGs25+gK8YSnhau/tpiFvJeZNE6vtaKO/yofqYD/e6L9MJPXpQ==')

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def decode64(value: str, size: int | None = None) -> bytes:
    result = base64.b64decode(value, validate=True)
    if size is not None and len(result) != size:
        raise ValueError(f'Expected {size} decoded bytes, got {len(result)}')
    return result

def validate_pairing(pair: dict) -> None:
    """Independently verify RSA-PSS/SHA256, using a 32-byte salt as CNG emits."""
    if pair.get('schema_version') != 1 or pair.get('platform') != 1:
        raise ValueError('Unsupported pairing schema or platform')
    if pair.get('signature_algorithm') != 'RSA-PSS-SHA256':
        raise ValueError('Unsupported signature algorithm')
    account = pair.get('account', '')
    if re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,29}', account) is None:
        raise ValueError('Account must be 3-30 lowercase ASCII name characters')
    modulus = decode64(pair['modulus_base64'], 256)
    if not modulus[0] & 0x80 or not modulus[-1] & 1:
        raise ValueError('Expected a 2048-bit odd RSA modulus')
    if decode64(pair['exponent_base64']) != b'\x01\x00\x01':
        raise ValueError('Only exponent 65537 is supported')
    signature = decode64(pair['signature_base64'], 256)
    message = b'\x01' + account.encode('utf-8')
    if decode64(pair['signed_message_base64']) != message:
        raise ValueError('Pairing account does not match its signed message')
    n, s = int.from_bytes(modulus, 'big'), int.from_bytes(signature, 'big')
    if s >= n:
        raise ValueError('RSA signature representative outside modulus')
    encoded = pow(s, 65537, n).to_bytes(256, 'big')
    if encoded[-1] != 0xbc or encoded[0] & 0x80:
        raise ValueError('Invalid PSS encoding')
    masked, digest = encoded[:223], encoded[223:255]
    mask = b''.join(hashlib.sha256(digest + i.to_bytes(4, 'big')).digest() for i in range(7))[:223]
    db = bytearray(a ^ b for a, b in zip(masked, mask))
    db[0] &= 0x7f
    if bytes(db[:190]) != bytes(190) or db[190] != 1:
        raise ValueError('Invalid PSS padding or salt length')
    expected = hashlib.sha256(bytes(8) + hashlib.sha256(message).digest() + db[191:]).digest()
    if not hmac.compare_digest(digest, expected):
        raise ValueError('Pairing signature is invalid')

def _pe_sections(data: bytes) -> list[tuple[int,int,int]]:
    if data[:2] != b'MZ':
        raise ValueError('Input is not a PE executable')
    pe = struct.unpack_from('<I', data, 60)[0]
    if data[pe:pe+4] != b'PE\0\0' or struct.unpack_from('<H', data, pe+4)[0] != 0x8664:
        raise ValueError('Expected AMD64 PE')
    count = struct.unpack_from('<H', data, pe+6)[0]
    start = pe + 24 + struct.unpack_from('<H', data, pe+20)[0]
    result = []
    for index in range(count):
        _, rva, size, offset = struct.unpack_from('<4I', data, start + 40*index + 8)
        result.append((rva,size,offset))
    return result

def _rva_offset(rva: int, sections: list[tuple[int,int,int]]) -> int:
    matches = [raw+rva-va for va,size,raw in sections if va <= rva < va+size]
    if len(matches) != 1:
        raise ValueError('Ambiguous or unmapped RVA')
    return matches[0]

def inspect_streamer(input_path: str | Path) -> dict:
    source = Path(input_path).resolve()
    data = source.read_bytes()
    _pe_sections(data)
    command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
               str(Path(__file__).parent/'Inspect-Streamer.ps1'), '-InputPath', str(source)]
    process = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace',
                             timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        report = json.loads(process.stdout)
    except (ValueError, TypeError) as error:
        raise ValueError('Streamer metadata inspector failed: '+process.stderr[-1000:]) from error
    if process.returncode or not report.get('compatible'):
        raise ValueError(report.get('error', 'Streamer contextual signatures did not match'))
    if report['source_sha256'] != sha256(data) or sha256(source.read_bytes()) != sha256(data):
        raise ValueError('Streamer input changed during metadata inspection')
    report['compatibility_scope'] = ('Eight unique named method bodies match normalized IL context; '
        'metadata token and RVA changes are supported. Changed method names, instructions, '
        'string constants, or exception flow require a reviewed profile. No runtime proof.')
    return report


def build_patch(data: bytes, modulus_b64: str, *, _inspection: dict | None = None) -> tuple[bytes,dict]:
    if _inspection is None:
        with tempfile.TemporaryDirectory(prefix='vd-streamer-inspect-') as temp:
            snapshot=Path(temp)/'VirtualDesktop.Streamer.exe'
            snapshot.write_bytes(data)
            inspection=inspect_streamer(snapshot)
    else:
        inspection=_inspection
    if not inspection.get('compatible') or inspection['source_sha256'] != sha256(data):
        raise ValueError('Metadata inspection does not match the supplied bytes')
    modulus = decode64(modulus_b64,256)
    if not modulus[0] & 0x80 or not modulus[-1] & 1:
        raise ValueError('Expected a 2048-bit odd modulus')
    if modulus_b64 == ORIGINAL_MODULUS:
        raise ValueError('A locally generated pairing modulus is required')
    sections = _pe_sections(data)
    specs = inspection['methods']
    roles={m['id']:m for m in specs}
    if len(specs)!=8 or set(roles) != {'registration','update_task','connectivity_probe','traceroute','startup','critical_updater','rsa_import','rsa_verify'}:
        raise ValueError('Incomplete or duplicate contextual method inspection')
    registration=roles['registration']['sites']
    ctor=registration['nullable_ctor']; from_result=registration['from_result']
    if ctor['opcode'] != 'newobj' or from_result['opcode'] != 'call':
        raise ValueError('Expected generic method token anchors')
    output = bytearray(data)
    edits = []
    def replace(offset: int, before: bytes, after: bytes, reason: str) -> None:
        if len(before) != len(after) or not before or data[offset:offset+len(before)] != before:
            raise ValueError(f'Byte anchor mismatch: {reason}')
        if any(offset < row['offset']+row['size'] and row['offset'] < offset+len(before) for row in edits):
            raise ValueError('Overlapping patches')
        output[offset:offset+len(before)] = after
        edits.append(dict(offset=offset,size=len(before),reason=reason,
                          before_sha256=sha256(before),after_sha256=sha256(after)))
    for spec in specs:
        role=spec['id']
        if role in {'rsa_import','rsa_verify'}:
            continue  # Guard authentication without altering its implementation.
        pos = _rva_offset(spec['rva'], sections)
        if (data[pos] & 3) != 3:
            raise ValueError('Expected guarded fat IL method header')
        header_size=(struct.unpack_from('<H',data,pos)[0] >> 12)*4
        if header_size != 12:
            raise ValueError('Unsupported IL method header size')
        code_size=struct.unpack_from('<I',data,pos+4)[0]
        if code_size != spec['code_size']:
            raise ValueError('Inspected and stored method lengths differ')
        code_pos=pos+header_size
        code=data[code_pos:code_pos+code_size]
        if len(code) != code_size:
            raise ValueError('Truncated method body')
        if role == 'registration':
            # Preserve the LAN listener and TaskCompletionSource setup, then
            # leave normally, executing the existing finally and SetResult.
            sites=spec['sites'];leave=sites['offline_leave']['offset'];target=sites['completion']['offset']
            first=sites['retry_first']['offset'];second=sites['retry_second']['offset']
            if code[leave] != 0x7e or code[first] != 0x28 or code[second] != 0x28 or second != first+5:
                raise ValueError('Unexpected registration instruction anchors')
            replace(code_pos+leave,code[leave:leave+5],b'\xdd'+struct.pack('<i',target-(leave+5)),
                    'Complete registration task after LAN startup; skip cloud registration')
            replace(code_pos+first,code[first:second+5],bytes(10),
                    'Remove automatic self-retry after offline completion; preserve event-driven reconnect')
        elif role == 'startup':
            # Only App.OnStartup's check of this modified executable is adapted.
            # Keep the global WinVerifyTrust helper and every other caller intact.
            site=spec['sites']['self_signature'];offset=site['offset']
            branch=spec['sites']['self_signature_branch']
            if code[offset] != 0x28 or site['size'] != 5 or branch['opcode'] not in {'brtrue','brtrue.s'}:
                raise ValueError('Unexpected startup self-signature instruction anchors')
            replace(code_pos+offset,code[offset:offset+5],b'\x26\x17\x00\x00\x00',
                    'Allow locally modified executable at the startup self-signature call only')
        else:
            if role == 'update_task':
                # Task.FromResult((bool?)true), existing MemberRef and MethodSpec.
                body = b'\x17\x73'+struct.pack('<I',ctor['token'])+b'\x28'+struct.pack('<I',from_result['token'])+b'\x2a'
                reason = 'Complete startup update task locally with true'
            elif role == 'connectivity_probe':
                body,reason = b'\x16\x2a','Disable HTTP HEAD internet connectivity probe'
            elif role == 'traceroute':
                body,reason = b'\x2a','Disable startup external traceroute probe'
            elif role == 'critical_updater':
                body = b'\x16\x73'+struct.pack('<I',ctor['token'])+b'\x2a'
                reason = 'Report no update without launching the external critical updater'
            else:
                raise ValueError('Unknown method role')
            # Retain RVA, allocated bytes and all metadata tokens. Shorten the
            # declared body, and clear MoreSects so no obsolete EH table or
            # unreachable trailing NOPs become part of the new method body.
            if len(body)>len(code):
                raise ValueError('Replacement cannot fit original method storage')
            header = bytearray(data[pos:pos+header_size])
            header[0] &= ~8
            struct.pack_into('<I',header,4,len(body))
            replace(pos,data[pos:pos+header_size],bytes(header),reason+' (header)')
            replace(code_pos,code,body+bytes(len(code)-len(body)),reason)
    old = ORIGINAL_MODULUS.encode('utf-16-le')
    new = modulus_b64.encode('utf-16-le')
    if data.count(old) != 1:
        raise ValueError('RSA modulus anchor is not unique')
    replace(data.index(old),old,new,'Trust this pairing key; preserve RSA-PSS signature and account checks')
    result = bytes(output)
    if len(result) != len(data) or len(edits) != 12:
        raise ValueError('Unexpected patch layout')
    # All untouched ranges must be byte-for-byte identical.
    cursor = 0
    for edit in sorted(edits,key=lambda item:item['offset']):
        if result[cursor:edit['offset']] != data[cursor:edit['offset']]:
            raise ValueError('Unexpected bytes changed outside patch ranges')
        cursor = edit['offset']+edit['size']
    if result[cursor:] != data[cursor:]:
        raise ValueError('Unexpected trailing bytes changed')
    report = dict(schema_version=2,version=inspection['version'],source_sha256=sha256(data),
                  compatibility=inspection,
                  output_sha256=sha256(result),size=len(result),patches=edits,
                  changed_byte_count=sum(a!=b for a,b in zip(data,result)),
                  runtime_tested=False,authenticode='Original publisher signature invalid after modification')
    return result,report

def patch_streamer(input_path: str | Path, output_path: str | Path, modulus_b64: str) -> dict:
    source, destination = Path(input_path), Path(output_path)
    report_path = destination.with_suffix(destination.suffix+'.patch.json')
    if source.resolve() == destination.resolve() or destination.exists() or report_path.exists():
        raise ValueError('Output must be a new path; originals and previous outputs are never overwritten')
    inspection=inspect_streamer(source)
    result,report = build_patch(source.read_bytes(),modulus_b64,_inspection=inspection)
    # Exclusive creation also protects against a path appearing after preflight.
    with destination.open('xb') as stream:
        stream.write(result)
    if sha256(destination.read_bytes()) != report['output_sha256']:
        raise IOError('Output verification failed')
    with report_path.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,indent=2)
        stream.write('\n')
    return report

def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--pairing',required=True,type=Path)
    args=parser.parse_args()
    pair=json.loads(args.pairing.read_text(encoding='utf-8-sig'))
    validate_pairing(pair)
    report=patch_streamer(args.input,args.output,pair['modulus_base64'])
    print(json.dumps({'output':str(args.output),'account':pair['account'],
                      'output_sha256':report['output_sha256'],'patch_regions':len(report['patches']),
                      'runtime_tested':False},indent=2))

if __name__=='__main__':
    main()
