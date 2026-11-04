import zipfile, struct, re, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent
APK = ROOT.parents[1] / 'apk-latest/base.apk'

def elf_sections(b):
    hdr = struct.unpack_from('<16sHHIQQQIHHHHHH', b)
    sections = [struct.unpack_from('<IIQQQQIIQQ', b, hdr[6] + i * hdr[11]) for i in range(hdr[12])]
    strings = sections[hdr[13]]
    strs = b[strings[4]:strings[4]+strings[5]]
    return hdr, [(strs[s[0]:strs.find(b'\0',s[0])].decode(), s) for s in sections]

with zipfile.ZipFile(APK) as z:
    for name in ['libassemblies.arm64-v8a.blob.so','libxamarin-app.so','libmonodroid.so','libmonosgen-2.0.so']:
        b=z.read('lib/arm64-v8a/'+name)
        (ROOT/name).write_bytes(b)
        hdr, sections = elf_sections(b)
        print(name, 'header', hdr)
        if name.startswith('libassemblies'):
            print('sections',sections)
            print('segments',[struct.unpack_from('<IIQQQQQQ',b,hdr[5]+i*hdr[9]) for i in range(hdr[10])])
        for n,s in sections:
            if s[1] == 11: # DYNSYM
                strsec = sections[s[6]][1]
                strs = b[strsec[4]:strsec[4]+strsec[5]]
                for o in range(s[4],s[4]+s[5],s[9]):
                    sym = struct.unpack_from('<IBBHQQ',b,o)
                    sn = strs[sym[0]:strs.find(b'\0',sym[0])].decode()
                    if re.search('application_config|aot_mode|app_environment|compressed_assemblies',sn):
                        sec = sections[sym[3]][1]
                        off = sym[4] - sec[3] + sec[4]
                        print('symbol',sn,sym,'offset',off,'bytes',b[off:off+min(100,sym[5])].hex())
        if name == 'libmonodroid.so':
            print('runtime strings',[s.decode(errors='replace') for s in re.findall(rb'[\x20-\x7e]{8,}',b) if re.search(rb'9\.0|35\.0|aot|AOT|assembly store',s)][:70])
        if name == 'libxamarin-app.so':
            for n,s in sections:
                if s[1] == 4:
                    for o in range(s[4],s[4]+s[5],s[9]):
                        off,info,addend = struct.unpack_from('<QQq',b,o)
                        if 134568 <= off < 134624:
                            target = next((ss for _,ss in sections if ss[3] <= addend < ss[3]+ss[5]),None)
                            if target:
                                at = addend-target[3]+target[4]
                                print('relocated string',off,b[at:b.find(b'\0',at)].decode(errors='replace'))
