"""Create a local APK signing identity, then sign or verify generated APKs."""
import argparse, hashlib, json, secrets, subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def tool_paths():
    tools=ROOT/'tools'
    java=next((tools/'java').glob('*/bin/java.exe'),None)
    keytool=next((tools/'java').glob('*/bin/keytool.exe'),None)
    signer=tools/'android/apksigner.jar'
    if not java or not keytool or not signer.exists():
        raise RuntimeError('Portable tools missing; run prepare_signing_tools.py first.')
    return java,keytool,signer

def run(args):
    subprocess.run([str(x) for x in args],check=True)

def init_key(directory):
    java,keytool,signer=tool_paths()
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    store=directory/'apk-signing.p12'
    password=directory/'apk-signing.password'
    cert=directory/'apk-signing.der'
    if not store.exists():
        if password.exists() or cert.exists():
            raise ValueError('Incomplete key directory: preserve or move it before creating a new key.')
        password.write_text(secrets.token_urlsafe(36),encoding='ascii')
        run([keytool,'-genkeypair','-keystore',store,'-storetype','PKCS12','-storepass:file',password,
             '-alias','vd-offline','-keyalg','RSA','-keysize','3072','-sigalg','SHA256withRSA',
             '-validity','3650','-dname','CN=Virtual Desktop Local Offline Build','-noprompt'])
    if not password.is_file():
        raise ValueError('Signing password file missing')
    temp_cert=directory/'apk-signing.der.tmp'
    if temp_cert.exists():
        raise ValueError('Unexpected temporary certificate already exists')
    run([keytool,'-exportcert','-keystore',store,'-storetype','PKCS12','-storepass:file',password,
         '-alias','vd-offline','-file',temp_cert])
    data=temp_cert.read_bytes()
    if cert.exists() and cert.read_bytes()!=data:
        raise ValueError('Existing certificate does not match signing key')
    temp_cert.replace(cert)
    signed_hash=1
    for b in data:
        signed_hash=(31*signed_hash+(b if b<128 else b-256))&0xffffffff
    if signed_hash>=2**31: signed_hash-=2**32
    record={'certificate_der':str(cert),'certificate_sha256':hashlib.sha256(data).hexdigest(),
            'certificate_sha1':hashlib.sha1(data).hexdigest(),'android_signature_hashcode':signed_hash,
            'dotnet_x509_hashcode':int.from_bytes(hashlib.sha1(data).digest()[:4],'big',signed=True)}
    (directory/'apk-signing-public.json').write_text(json.dumps(record,indent=2))
    print(json.dumps(record,indent=2))

def sign(apk,out,directory):
    java,_,signer=tool_paths()
    apk=Path(apk); out=Path(out); directory=Path(directory)
    if out.exists(): raise ValueError('Refusing to overwrite output APK')
    run([java,'-jar',signer,'sign','--ks',directory/'apk-signing.p12','--ks-type','PKCS12',
         '--ks-key-alias','vd-offline','--ks-pass','file:'+str(directory/'apk-signing.password'),
         '--v1-signing-enabled','true','--v2-signing-enabled','true','--v3-signing-enabled','true',
         '--v4-signing-enabled','false','--out',out,apk])
    verify(out)

def verify(apk):
    java,_,signer=tool_paths()
    run([java,'-jar',signer,'verify','--verbose','--print-certs',apk])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--directory',type=Path,required=True)
    s=sub.add_parser('sign');s.add_argument('--apk',type=Path,required=True);s.add_argument('--output',type=Path,required=True);s.add_argument('--directory',type=Path,required=True)
    v=sub.add_parser('verify');v.add_argument('--apk',type=Path,required=True)
    a=p.parse_args()
    if a.command=='init':init_key(a.directory)
    elif a.command=='sign':sign(a.apk,a.output,a.directory)
    else:verify(a.apk)
