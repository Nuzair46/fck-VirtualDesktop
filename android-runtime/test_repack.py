"""Checks against the real input store, with deliberately corrupted controls."""
import hashlib, json, struct, tempfile, zipfile, subprocess, shutil
from pathlib import Path
import repack_apk as r
import signing

ROOT=Path(__file__).resolve().parent
APK=ROOT.parents[1]/'apk-latest/base.apk'
results=[]

def checked(name,fn):
    fn(); results.append({'test':name,'result':'pass'})

def rejects(fn):
    try: fn()
    except (ValueError,struct.error,StopIteration): return
    raise AssertionError('Corruption unexpectedly accepted')

def synthetic_store(images):
    """Create a smaller valid v2 store to exercise count-independent parsing."""
    count=len(images)
    header=struct.pack('<5I',0x41424158,0x80010002,count,count*2,count*24)
    indexes=b''.join(struct.pack('<QI',i+1,i//2) for i in range(count*2))
    names=b''.join(struct.pack('<I',len(name.encode()))+name.encode() for name in images)
    cursor=len(header)+len(indexes)+count*28+len(names)
    descriptors=[];payload=[]
    for i,image in enumerate(images.values()):
        descriptors.append(struct.pack('<7I',i,cursor,len(image),0,0,0,0))
        payload.append(image);cursor+=len(image)
    return header+indexes+b''.join(descriptors)+names+b''.join(payload)

def main():
    with zipfile.ZipFile(APK) as z:
        carrier=z.read(r.STORE)
        runtime=z.read('lib/arm64-v8a/libxamarin-app.so')
        manifest=z.read('AndroidManifest.xml')
        required={name:z.read(name) for name in r.REQUIRED_MEMBERS}
    original=r.store_from_elf(carrier)
    start,cursor,names,descs=r.parse_store(original)
    images={name:r.uncompressed_image(original[d[1]:d[1]+d[2]]) for name,d in zip(names,descs)}
    replacements={name:images[name] for name in r.ALLOWED}
    rebuilt=r.replace_store(original,replacements)
    _,_,new_names,new_descs=r.parse_store(rebuilt)
    decoded={name:r.uncompressed_image(rebuilt[d[1]:d[1]+d[2]]) for name,d in zip(new_names,new_descs)}
    def compare_images():
        assert images==decoded and len(decoded)==185
    checked('raw replacement store preserves all185 decoded managed images',compare_images)
    checked('assembly name/hash index bytes preserved',lambda: check_equal(original[:start],rebuilt[:start]))
    output=r.replace_elf_payload(carrier,rebuilt)
    checked('rebuilt ELF payload parses back exactly',lambda: check_equal(r.store_from_elf(output),rebuilt))
    checked('ELF load data unchanged except section-header location',lambda: check_equal(carrier[:40]+carrier[48:16384],output[:40]+output[48:16384]))
    bad=bytearray(original);bad[0]^=1
    checked('wrong store magic rejected',lambda:rejects(lambda:r.parse_store(bad)))
    bad2=bytearray(original);struct.pack_into('<I',bad2,start+4,len(original)+1)
    checked('out-of-bounds assembly descriptor rejected',lambda:rejects(lambda:r.parse_store(bad2)))
    checked('original Mono normal mode verified via export and RELA',lambda:r.assert_normal_aot(runtime))
    bad_runtime=runtime.replace(b'normal\0',b'full\0\0\0')
    checked('non-normal AOT mode rejected',lambda:rejects(lambda:r.assert_normal_aot(bad_runtime)))
    with tempfile.TemporaryDirectory(prefix='vd_repack_') as td:
        bad_apk=Path(td)/'wrong.apk';bad_apk.write_bytes(b'wrong')
        checked('malformed APK ZIP container rejected',lambda:rejects(lambda:r.check_apk(bad_apk)))
        changed=Path(td)/'metadata-change.apk'
        shutil.copyfile(APK,changed)
        original_hash=r.sha256_file(APK)
        with zipfile.ZipFile(changed,'a') as z:z.comment=b'Non-code ZIP metadata compatibility test'
        def check_metadata_change():
            actual=r.check_apk(changed)
            assert actual!=original_hash
            assert actual==r.sha256_file(changed)
        checked('whole APK hash change from ZIP comment alone remains compatible',check_metadata_change)
        checked('prepared-session hash still rejects later input changes',lambda:rejects(lambda:r.check_apk(changed,original_hash)))
        smaller=synthetic_store({n:images[n] for n in sorted(r.ALLOWED)})
        def check_variable_count():
            _,_,small_names,small_descriptors=r.parse_store(smaller)
            assert len(small_names)==2 and len(small_descriptors)==2
            rebuilt_small=r.replace_store(smaller,{n:images[n] for n in r.ALLOWED})
            _,_,new_names,new_descriptors=r.parse_store(rebuilt_small)
            assert small_names==new_names
            for name,desc in zip(new_names,new_descriptors):
                assert r.uncompressed_image(rebuilt_small[desc[1]:desc[1]+desc[2]])==images[name]
        checked('v2 store assembly count can vary with complete metadata coverage',check_variable_count)
        broken_index=bytearray(smaller);struct.pack_into('<I',broken_index,28,99)
        checked('out-of-range name-hash index target rejected',lambda:rejects(lambda:r.parse_store(broken_index)))
        wrong_runtime=Path(td)/'wrong-runtime.apk'
        with zipfile.ZipFile(wrong_runtime,'w') as z:
            for name,data in required.items():
                z.writestr(name,data.replace(b'normal\0',b'full\0\0\0') if name==r.RUNTIME else data)
        checked('full-AOT APK rejected regardless of its whole-file digest',lambda:rejects(lambda:r.check_apk(wrong_runtime)))
        wrong_arch=bytearray(carrier);struct.pack_into('<H',wrong_arch,18,62)
        checked('non-ARM64 carrier rejected',lambda:rejects(lambda:r.store_from_elf(wrong_arch)))
        misaligned=Path(td)/'unaligned.apk'
        with zipfile.ZipFile(misaligned,'w') as z:z.writestr('lib/arm64-v8a/test.so',b'ELF')
        checked('misaligned native ZIP entry rejected',lambda:rejects(lambda:r.check_alignment(misaligned)))
        def check_alignment_extra():
            for align in (4,16384):
                for offset in (0,1,2,3,4,5,6,16383,16384):
                    extra=r.alignment_extra(offset,align)
                    marker,size,value=struct.unpack_from('<HHH',extra)
                    assert (marker,size,value)==(0xd935,len(extra)-4,align)
                    assert (offset+len(extra))%align==0
        checked('alignment metadata contains required uint16 for all boundary offsets',check_alignment_extra)
        small=Path(td)/'signer-alignment.unsigned.apk'
        with zipfile.ZipFile(small,'w') as z:
            for name,data,alignment in [('AndroidManifest.xml',manifest,4),('lib/arm64-v8a/test.so',b'ELF alignment regression test',16384)]:
                info=zipfile.ZipInfo(name)
                info.extra=r.alignment_extra(z.fp.tell()+30+len(name.encode()),alignment)
                z.writestr(info,data)
        signed=Path(td)/'signer-alignment.signed.apk'
        signing.sign(small,signed,ROOT/'keys')
        checked('official signer retains native16KiB alignment after adding JAR records',lambda:subprocess.run([str(ROOT/'tools/android/zipalign.exe'),'-c','-P','16','4',str(signed)],check=True))
        checked('signed APK also passes independent offset parser',lambda:r.check_alignment(signed))
    report={'tests':results,'all_passed':True,'device_runtime_tested':False}
    (ROOT/'repack-test-results.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

def check_equal(a,b):
    assert a==b

if __name__=='__main__':main()
