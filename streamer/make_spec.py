"""Development-only: record exact byte anchors from reviewed, original input."""
import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).parent
data = Path(r'C:\Program Files\Virtual Desktop Streamer\VirtualDesktop.Streamer.exe').read_bytes()
assert hashlib.sha256(data).hexdigest() == '0229a322137f55687240d2b51f5f7002056d9277d7fa001ec6cb4eb25af0bfb8'
pe = struct.unpack_from('<I', data, 60)[0]
count = struct.unpack_from('<H', data, pe + 6)[0]
section = pe + 24 + struct.unpack_from('<H', data, pe + 20)[0]
sections = []
for index in range(count):
    off = section + 40 * index
    vsize, va, size, raw = struct.unpack_from('<4I', data, off + 8)
    sections.append((va, size, raw))
def offset(rva):
    return next(raw + rva - va for va, size, raw in sections if va <= rva < va + size)

methods = json.loads((ROOT/'evidence/metadata.json').read_text(encoding='utf-8-sig'))
selected = []
for token in [100663726, 100664607, 100663691, 100663692, 100664639, 100664605]:
    method = next(item for item in methods if item['token'] == token)
    pos = offset(method['rva'])
    flags = struct.unpack_from('<H', data, pos)[0]
    assert flags & 3 == 3
    header = (flags >> 12)*4
    code = data[pos+header:pos+header+method['size']]
    selected.append(dict(token=hex(token), rva=method['rva'], header_offset=pos, header_size=header,
                         code_size=method['size'], code_sha256=hashlib.sha256(code).hexdigest(),
                         header_hex=data[pos:pos+header].hex()))
(ROOT/'streamer-spec.json').write_text(json.dumps({'version':'1.34.25.0','methods':selected},indent=2)+'\n')
print(json.dumps(selected,indent=2))
