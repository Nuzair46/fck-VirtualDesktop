"""Independently audit a signed generated APK against original input and patched IL."""
import argparse,hashlib,json,subprocess,zipfile
from pathlib import Path
import repack_apk as r
import signing

def digest_member(archive,name):
    with archive.open(name) as f:return hashlib.file_digest(f,'sha256').hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--original',type=Path,required=True)
    p.add_argument('--patched',type=Path,required=True)
    p.add_argument('--assemblies',type=Path,required=True)
    p.add_argument('--certificate',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--expected-original-sha256')
    a=p.parse_args()
    original_sha256=r.check_apk(a.original,a.expected_original_sha256)
    java,_,signer=signing.tool_paths()
    verified=subprocess.run([str(java),'-jar',str(signer),'verify','--verbose','--print-certs',str(a.patched)],check=True,capture_output=True,text=True)
    cert_hash=hashlib.sha256(a.certificate.read_bytes()).hexdigest()
    if 'Signer #1 certificate SHA-256 digest: '+cert_hash not in verified.stdout:
        raise ValueError('Signer certificate mismatch')
    if 'Number of signers: 1' not in verified.stdout:raise ValueError('Unexpected signer count')
    subprocess.run([str(signing.ROOT/'tools/android/zipalign.exe'),'-c','-P','16','4',str(a.patched)],check=True)
    r.check_alignment(a.patched)
    omitted={'lib/arm64-v8a/libaot-'+name+'.so' for name in r.ALLOWED}
    checked_members=0;checked_assemblies=0
    with zipfile.ZipFile(a.original) as before,zipfile.ZipFile(a.patched) as after:
        if len(after.namelist())!=len(set(after.namelist())):raise ValueError('Generated APK contains duplicate ZIP members')
        allowed_original={n for n in before.namelist() if not r.is_signature(n) and n not in omitted}
        allowed_patched={n for n in after.namelist() if not r.is_signature(n)}
        if allowed_original!=allowed_patched:raise ValueError('APK member list differs unexpectedly')
        for name in sorted(allowed_original-{r.STORE}):
            if digest_member(before,name)!=digest_member(after,name):raise ValueError('Untouched ZIP member changed: '+name)
            checked_members+=1
        left=r.store_from_elf(before.read(r.STORE));right=r.store_from_elf(after.read(r.STORE))
        _,_,names,descs=r.parse_store(left);_,_,new_names,new_descs=r.parse_store(right)
        if names!=new_names:raise ValueError('Store assembly names changed')
        for name,d,nd in zip(names,descs,new_descs):
            expected=(a.assemblies/name).read_bytes() if name in r.ALLOWED else r.uncompressed_image(left[d[1]:d[1]+d[2]])
            actual=r.uncompressed_image(right[nd[1]:nd[1]+nd[2]])
            if expected!=actual:raise ValueError('Final managed assembly mismatch: '+name)
            checked_assemblies+=1
    result={'original_apk_sha256':original_sha256,'apk_sha256':r.sha256_file(a.patched),'signer_sha256':cert_hash,'signature_verification':verified.stdout.splitlines(),
            'zipalign_16k_passed':True,'unchanged_zip_members_sha256_verified':checked_members,
            'managed_assemblies_byte_verified':checked_assemblies,'omitted_aot_companions':sorted(omitted),
            'runtime_tested':False}
    a.report.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
