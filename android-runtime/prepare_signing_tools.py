"""Fetch portable official Java and Android signing tools into this project."""
from pathlib import Path
import hashlib, json, urllib.request, zipfile

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / 'tools'

def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent':'VirtualDesktopOfflineBuild/1.0'})
    with urllib.request.urlopen(request, timeout=180) as r:
        return r.read()

def download(url, name, digest, algorithm):
    path = TOOLS / name
    if path.exists() and hashlib.new(algorithm, path.read_bytes()).hexdigest() == digest:
        return path
    data = fetch(url)
    if hashlib.new(algorithm, data).hexdigest() != digest:
        raise ValueError('Download digest mismatch: '+name)
    path.write_bytes(data)
    return path

def main():
    TOOLS.mkdir(parents=True,exist_ok=True)
    # Resolved from the official Adoptium API and Android SDK repository metadata
    # on 2026-11-04. Keep the build reproducible instead of following 'latest'.
    jre = {'name':'OpenJDK21U-jre_x64_windows_hotspot_21.0.13_9.zip',
           'link':'https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jre_x64_windows_hotspot_21.0.13_9.zip',
           'checksum':'2332c0b4cff6bed722da3567c91d5e47568bcba863fa4945d0a8d6fc76158628'}
    jre_zip=download(jre['link'],jre['name'],jre['checksum'],'sha256')
    with zipfile.ZipFile(jre_zip) as z:
        z.extractall(TOOLS / 'java')
    checksum='5753c679a1b90bcf6fbc9945a2ce39dfb9e74f1df0831a1e1866ae5b594326f0'
    url='https://dl.google.com/android/repository/build-tools_r35_windows.zip'
    sdk_zip=download(url,'build-tools-35.0.0-windows.zip',checksum,'sha256')
    with zipfile.ZipFile(sdk_zip) as z:
        for info in z.infolist():
            if info.filename.endswith(('apksigner.jar','zipalign.exe','aapt2.exe','.dll','NOTICE.txt','source.properties')):
                target=TOOLS/'android'/Path(info.filename).name
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(z.read(info))
    java=next((TOOLS/'java').rglob('java.exe'))
    keytool=next((TOOLS/'java').rglob('keytool.exe'))
    manifest={'java':java.relative_to(TOOLS).as_posix(),'keytool':keytool.relative_to(TOOLS).as_posix(),
              'apksigner':'android/apksigner.jar','zipalign':'android/zipalign.exe','aapt2':'android/aapt2.exe',
              'downloads':[{'url':jre['link'],'sha256':jre['checksum']}, {'url':url,'sha256':checksum}]}
    (TOOLS/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(manifest,indent=2))

if __name__=='__main__': main()
