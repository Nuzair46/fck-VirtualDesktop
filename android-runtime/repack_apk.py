"""Extract or rebuild a structurally supported VD Xamarin assembly store and APK.

All operations use caller-provided original files; installed applications are never
changed. Rebuilt APKs are unsigned. Run signing.py afterwards. Native .so entries
are stored and aligned to 16 KiB. The two modified assembly AOT companions are
omitted so the packaged Mono 'normal' runtime JITs the replacement IL.
"""
from pathlib import Path
import argparse, copy, hashlib, json, os, shutil, struct, zipfile
from collections import Counter

STORE='lib/arm64-v8a/libassemblies.arm64-v8a.blob.so'
ALLOWED={'VirtualDesktop.Android.dll','VirtualDesktop.Mobile.dll'}
RUNTIME='lib/arm64-v8a/libxamarin-app.so'
REQUIRED_MEMBERS={STORE,RUNTIME,'AndroidManifest.xml','lib/arm64-v8a/libmonosgen-2.0.so','lib/arm64-v8a/libmonodroid.so'}

def sha256_file(path):
    with open(path,'rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()

def check_apk(path,expected_sha256=None):
    """Validate the container/runtime profile and optionally bind a session hash.

    Manifest package/version identity and method-pattern compatibility are
    validated by the build orchestrator. A changed whole-file digest alone is
    not a compatibility failure.
    """
    digest=sha256_file(path)
    if expected_sha256 is not None and digest!=expected_sha256:
        raise ValueError('APK changed after selection: prepared-session SHA-256 mismatch')
    try:
        with zipfile.ZipFile(path) as z:
            entries=z.infolist()
            names=[i.filename for i in entries]
            if len(set(names))!=len(names):raise ValueError('Duplicate APK ZIP members are unsupported')
            if not REQUIRED_MEMBERS<=set(names):raise ValueError('APK does not contain the supported ARM64 Mono assembly-store runtime')
            for name in REQUIRED_MEMBERS:
                info=z.getinfo(name)
                if info.flag_bits&1 or info.file_size>512*1024*1024:
                    raise ValueError('Unsupported encrypted or oversized runtime member')
            assert_normal_aot(z.read(RUNTIME))
            for name in ('lib/arm64-v8a/libmonosgen-2.0.so','lib/arm64-v8a/libmonodroid.so'):
                elf_layout(z.read(name))
            carrier=z.read(STORE)
            store=store_from_elf(carrier)
            _,_,assemblies,_=parse_store(store)
            if not ALLOWED<=set(assemblies):raise ValueError('Expected Virtual Desktop managed assemblies are missing')
            # This is a structural compatibility check; it also verifies that
            # the payload can be replaced without moving any loaded ELF data.
            replace_elf_payload(carrier,store)
    except zipfile.BadZipFile as exc:
        raise ValueError('Input is not a valid APK ZIP container') from exc
    return digest

def elf_layout(data):
    if len(data)<64:raise ValueError('ELF header is truncated')
    hdr=struct.unpack_from('<16sHHIQQQIHHHHHH',data)
    if hdr[0][:6]!=b'\x7fELF\x02\x01' or hdr[2]!=183 or hdr[11]!=64 or hdr[8]!=64:
        raise ValueError('Expected little-endian ARM64 ELF64')
    if not 0<hdr[12]<=65535 or hdr[13]>=hdr[12] or hdr[6]<64 or hdr[6]+hdr[12]*64>len(data):
        raise ValueError('ELF section-header table is invalid')
    if hdr[10] and (hdr[9]!=56 or hdr[5]<64 or hdr[5]+hdr[10]*56>len(data)):
        raise ValueError('ELF program-header table is invalid')
    sections=[list(struct.unpack_from('<IIQQQQIIQQ',data,hdr[6]+i*64)) for i in range(hdr[12])]
    if any(s[1]!=8 and s[4]+s[5]>len(data) for s in sections):
        raise ValueError('ELF section exceeds file bounds')
    strings=sections[hdr[13]]
    if strings[1]!=3:raise ValueError('ELF section-name table is not a string table')
    names=data[strings[4]:strings[4]+strings[5]]
    named=[]
    for section in sections:
        if section[0]>=len(names) or names.find(b'\0',section[0])<0:
            raise ValueError('Invalid ELF section name')
        named.append((names[section[0]:names.index(b'\0',section[0])].decode(),section))
    return hdr,sections,named

def assert_normal_aot(data):
    hdr,sections,named=elf_layout(data)
    symbol=None
    for _,sec in named:
        if sec[1]!=11: continue
        if sec[9]!=24 or sec[5]%24 or sec[6]>=len(sections):raise ValueError('Unsupported ELF dynamic symbol table')
        stringsec=sections[sec[6]]
        if stringsec[1]!=3:raise ValueError('Invalid ELF dynamic symbol strings')
        strings=data[stringsec[4]:stringsec[4]+stringsec[5]]
        for off in range(sec[4],sec[4]+sec[5],sec[9]):
            sym=struct.unpack_from('<IBBHQQ',data,off)
            if sym[0]>=len(strings) or strings.find(b'\0',sym[0])<0:
                raise ValueError('Invalid ELF dynamic symbol name')
            name=strings[sym[0]:strings.index(b'\0',sym[0])].decode()
            if name=='mono_aot_mode_name':
                if symbol is not None:raise ValueError('Ambiguous Mono AOT mode export')
                symbol=sym[4]
    if symbol is None: raise ValueError('Mono AOT mode export missing')
    modes=[]
    for _,sec in named:
        if sec[1]!=4: continue
        if sec[9]!=24 or sec[5]%24:raise ValueError('Unsupported ARM64 ELF relocation table')
        for off in range(sec[4],sec[4]+sec[5],sec[9]):
            address,info,target=struct.unpack_from('<QQq',data,off)
            if address!=symbol:continue
            if info!=1027:raise ValueError('Unsupported Mono AOT mode relocation type')
            candidates=[s for s in sections if s[3]<=target<s[3]+s[5] and s[1]!=8]
            if len(candidates)!=1:raise ValueError('Mono AOT mode relocation does not address one defined section')
            target_sec=candidates[0]
            pos=target-target_sec[3]+target_sec[4]
            end=data.find(b'\0',pos,min(pos+64,target_sec[4]+target_sec[5]))
            if end<0:raise ValueError('Mono AOT mode string is invalid')
            modes.append(data[pos:end].decode())
    if modes!=['normal']:raise ValueError('Unsupported Mono AOT mode: '+repr(modes))

def store_from_elf(data):
    hdr,sections,named=elf_layout(data)
    candidates=[s for n,s in named if n=='payload']
    if len(candidates)!=1:raise ValueError('Expected exactly one assembly-store ELF payload')
    payload=candidates[0]
    if payload[1]!=1 or payload[2]!=0 or payload[4]<16384 or payload[4]%16384 or payload[8]<16384 or payload[8]&(payload[8]-1):
        raise ValueError('Unexpected assembly store ELF payload layout')
    return data[payload[4]:payload[4]+payload[5]]

def parse_store(data):
    if len(data)<20:raise ValueError('Truncated XABA store header')
    magic,version,count,index_count,index_size=struct.unpack_from('<5I',data)
    if magic!=0x41424158 or version!=0x80010002 or not 1<=count<=65535 or index_count!=count*2 or index_size!=index_count*12:
        raise ValueError('Unsupported XABA assembly store header')
    start=20+index_size
    if start+count*32>len(data):raise ValueError('Assembly store metadata tables are truncated')
    indexes=[struct.unpack_from('<QI',data,20+i*12) for i in range(index_count)]
    if any(indexes[i][0]>=indexes[i+1][0] for i in range(len(indexes)-1)):
        raise ValueError('Assembly name-hash index is unsorted or ambiguous')
    if Counter(target for _,target in indexes)!=Counter({i:2 for i in range(count)}):
        raise ValueError('Assembly name-hash index does not cover every descriptor twice')
    descriptors=[list(struct.unpack_from('<7I',data,start+i*28)) for i in range(count)]
    if sorted(d[0] for d in descriptors)!=list(range(count)):
        raise ValueError('Assembly runtime mapping indexes are not a unique permutation')
    cursor=start+count*28
    names=[]
    for _ in range(count):
        if cursor+4>len(data):raise ValueError('Assembly name table is truncated')
        length=struct.unpack_from('<I',data,cursor)[0];cursor+=4
        if not 0<length<1024:raise ValueError('Invalid assembly name length')
        if cursor+length>len(data):raise ValueError('Assembly name exceeds store bounds')
        name=data[cursor:cursor+length].decode('utf-8');cursor+=length
        if not name or any(c in name for c in '\\\x00<>:"|?*') or any(p in ('','.','..') for p in name.split('/')):
            raise ValueError('Unsafe assembly name')
        names.append(name if name.endswith('.dll') else name+'.dll')
    if len(set(names))!=count:raise ValueError('Duplicate assembly names')
    for desc in descriptors:
        if not desc[2]:raise ValueError('Assembly descriptor has an empty image')
        for pos in (1,3,5):
            offset,size=desc[pos:pos+2]
            if size and (offset<cursor or offset+size>len(data)):
                raise ValueError('Assembly descriptor outside store')
    return start,cursor,names,descriptors

def lz4_block(data,expected):
    out=bytearray();pos=0
    def length(value):
        nonlocal pos
        if value==15:
            while True:
                extra=data[pos];pos+=1;value+=extra
                if extra!=255:break
        return value
    while pos<len(data):
        token=data[pos];pos+=1
        literals=length(token>>4)
        if pos+literals>len(data):raise ValueError('LZ4 literal overflow')
        out.extend(data[pos:pos+literals]);pos+=literals
        if pos==len(data):break
        offset=int.from_bytes(data[pos:pos+2],'little');pos+=2
        if not 0<offset<=len(out):raise ValueError('LZ4 match offset invalid')
        match=length(token&15)+4
        if len(out)+match>expected:raise ValueError('LZ4 match overflow')
        pattern=out[-offset:]
        out.extend((pattern*((match+offset-1)//offset))[:match])
    if len(out)!=expected:raise ValueError('LZ4 decoded length mismatch')
    return bytes(out)

def uncompressed_image(data):
    if data[:4]==b'XALZ':
        if len(data)<12:raise ValueError('Truncated compressed assembly header')
        _,expected=struct.unpack_from('<II',data,4)
        if not 64<=expected<=512*1024*1024:raise ValueError('Unsupported decompressed assembly size')
        data=lz4_block(data[12:],expected)
    if len(data)<64 or data[:2]!=b'MZ' or data[struct.unpack_from('<I',data,0x3c)[0]:][:4]!=b'PE\0\0':
        raise ValueError('Assembly image is not PE')
    return data

def extract(apk,output,expected_sha256=None):
    input_sha256=check_apk(apk,expected_sha256)
    if output.exists() and any(output.iterdir()):raise ValueError('Extraction output must be empty')
    output.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(apk) as z:
        data=store_from_elf(z.read(STORE))
        assert_normal_aot(z.read('lib/arm64-v8a/libxamarin-app.so'))
    _,_,names,descs=parse_store(data)
    records=[]
    for name,d in zip(names,descs):
        image=uncompressed_image(data[d[1]:d[1]+d[2]])
        (output/name).parent.mkdir(parents=True,exist_ok=True)
        (output/name).write_bytes(image)
        records.append({'name':name,'size':len(image),'sha256':hashlib.sha256(image).hexdigest()})
    (output/'extraction.json').write_text(json.dumps(records,indent=2))
    print(json.dumps({'extracted':len(records),'directory':str(output),'input_sha256':input_sha256}))

def replace_store(data,replacements):
    start,cursor,names,descs=parse_store(data)
    if not set(replacements)<=set(names):raise ValueError('Replacement assembly not in original store')
    result=bytearray(data[:cursor])
    for i,(name,d) in enumerate(zip(names,descs)):
        for pos in (1,3,5):
            offset,size=d[pos:pos+2]
            if not size:continue
            image=data[offset:offset+size]
            if pos==1 and name in replacements:
                image=uncompressed_image(replacements[name])
            result.extend(b'\0'*(-len(result)%4))
            d[pos:pos+2]=[len(result),len(image)]
            result.extend(image)
        struct.pack_into('<7I',result,start+i*28,*d)
    _,_,new_names,new_descs=parse_store(result)
    for name,d in zip(new_names,new_descs):
        if name in replacements and result[d[1]:d[1]+d[2]]!=replacements[name]:
            raise ValueError('Replacement verification failed')
    return bytes(result)

def replace_elf_payload(data,payload):
    hdr,sections,named=elf_layout(data)
    section=next(s for n,s in named if n=='payload')
    old_start,old_size=section[4:6]
    if hdr[6]+hdr[12]*64!=len(data) or not 0<=hdr[6]-(old_start+old_size)<8:
        raise ValueError('Unexpected trailing data after ELF payload')
    for _,s in named:
        if s is not section and s[1]!=8 and s[4]+s[5]>old_start:
            raise ValueError('Other ELF section overlaps or follows payload')
    for i in range(hdr[10]):
        ph=struct.unpack_from('<IIQQQQQQ',data,hdr[5]+i*hdr[9])
        if ph[2]+ph[5]>old_start:raise ValueError('ELF segment overlaps payload')
    section[5]=len(payload)
    result=bytearray(data[:old_start]);result.extend(payload)
    result.extend(b'\0'*(-len(result)%8))
    struct.pack_into('<Q',result,40,len(result))
    for s in sections:result.extend(struct.pack('<IIQQQQIIQQ',*s))
    if store_from_elf(result)!=payload:raise ValueError('ELF payload verification failed')
    return bytes(result)

def is_signature(name):
    upper=name.upper()
    if not upper.startswith('META-INF/'):return False
    leaf=upper[9:]
    return '/' not in leaf and (leaf=='MANIFEST.MF' or leaf.endswith(('.SF','.RSA','.DSA','.EC')) or leaf.startswith('SIG-'))

def check_alignment(apk):
    with zipfile.ZipFile(apk) as z,open(apk,'rb') as f:
        for i in z.infolist():
            if i.compress_type!=zipfile.ZIP_STORED:continue
            f.seek(i.header_offset+26)
            name_len,extra_len=struct.unpack('<HH',f.read(4))
            offset=i.header_offset+30+name_len+extra_len
            alignment=16384 if i.filename.startswith('lib/') and i.filename.endswith('.so') else 4
            if offset%alignment:raise ValueError('Unaligned ZIP member: '+i.filename)

def alignment_extra(data_offset_without_extra,alignment):
    padding=(-data_offset_without_extra)%alignment
    # Android's0xd935 field begins with a uint16 alignment, then padding.
    # Record the requirement even if the entry is already aligned, because the
    # signer may insert records before it and must know how to realign it.
    while padding<6:padding+=alignment
    return struct.pack('<HHH',0xd935,padding-4,alignment)+b'\0'*(padding-6)

def repack(apk,assemblies,output,expected_sha256=None):
    input_sha256=check_apk(apk,expected_sha256)
    if output.exists():raise ValueError('Refusing to overwrite APK output')
    replacements={p.name:p.read_bytes() for p in assemblies.glob('*.dll')}
    if set(replacements)!=ALLOWED:
        raise ValueError('Replacement folder must contain exactly Android and Mobile DLLs')
    omitted={'lib/arm64-v8a/libaot-'+name+'.so' for name in replacements}
    temporary=output.with_suffix(output.suffix+'.building')
    if temporary.exists():raise ValueError('Temporary output already exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(apk) as zin:
        assert_normal_aot(zin.read('lib/arm64-v8a/libxamarin-app.so'))
        original=zin.read(STORE)
        updated=replace_elf_payload(original,replace_store(store_from_elf(original),replacements))
        old_records=[];new_records=[]
        with zipfile.ZipFile(temporary,'w',allowZip64=True) as zout:
            for info in zin.infolist():
                if info.filename in omitted or is_signature(info.filename):continue
                clone=copy.copy(info)
                clone.extra=b''
                clone.flag_bits=0
                if info.filename.startswith('lib/') and info.filename.endswith('.so'):
                    clone.compress_type=zipfile.ZIP_STORED
                if clone.compress_type==zipfile.ZIP_STORED:
                    align=16384 if info.filename.startswith('lib/') and info.filename.endswith('.so') else 4
                    clone.extra=alignment_extra(zout.fp.tell()+30+len(info.filename.encode('utf-8')),align)
                if info.filename==STORE:
                    clone.file_size=len(updated)
                    zout.writestr(clone,updated)
                else:
                    with zin.open(info) as src,zout.open(clone,'w') as dst:
                        shutil.copyfileobj(src,dst,1024*1024)
        check_alignment(temporary)
        with zipfile.ZipFile(temporary) as check:
            bad=check.testzip()
            if bad:raise ValueError('ZIP CRC mismatch: '+bad)
            expected={i.filename for i in zin.infolist() if i.filename not in omitted and not is_signature(i.filename)}
            if set(check.namelist())!=expected:raise ValueError('ZIP member set mismatch')
            for name in expected-{STORE}:
                old=zin.getinfo(name);new=check.getinfo(name)
                if (old.CRC,old.file_size)!=(new.CRC,new.file_size):raise ValueError('Unintended member change: '+name)
            rebuilt=store_from_elf(check.read(STORE))
            _,_,names,descs=parse_store(rebuilt)
            for name,d in zip(names,descs):
                if name in replacements and uncompressed_image(rebuilt[d[1]:d[1]+d[2]])!=replacements[name]:
                    raise ValueError('Final APK replacement mismatch')
    temporary.replace(output)
    record={'input_sha256':input_sha256,'unsigned_apk':str(output),'unsigned_sha256':sha256_file(output),
            'modified_assemblies':{n:hashlib.sha256(b).hexdigest() for n,b in replacements.items()},
            'omitted_aot_companions':sorted(omitted),'mono_aot_mode':'normal','native_zip_alignment':16384,
            'signing':'unsigned','runtime_tested':False}
    output.with_suffix(output.suffix+'.json').write_text(json.dumps(record,indent=2))
    print(json.dumps(record,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    e=sub.add_parser('extract');e.add_argument('--apk',type=Path,required=True);e.add_argument('--output',type=Path,required=True)
    r=sub.add_parser('repack');r.add_argument('--apk',type=Path,required=True);r.add_argument('--assemblies',type=Path,required=True);r.add_argument('--output',type=Path,required=True)
    e.add_argument('--expected-sha256');r.add_argument('--expected-sha256')
    a=p.parse_args()
    if a.command=='extract':extract(a.apk,a.output,a.expected_sha256)
    else:repack(a.apk,a.assemblies,a.output,a.expected_sha256)
