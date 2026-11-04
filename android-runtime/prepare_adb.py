"""Provision the official pinned Windows ADB tools into the portable bundle.

This prepares files only. It never starts an ADB server, connects to a device,
or changes a headset. End users receive the already provisioned tools.
"""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parent
TOOLS=ROOT/'tools'
REPOSITORY='https://dl.google.com/android/repository/repository2-1.xml'
PIN={'version':'37.0.1',
     'url':'https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip',
     'sha1':'e03e78b1d80b396f1c3358e31251cb31740e1110','size':8044989,
     'sha256':'45f4d63113e895ebde0c90f194099a4676b6ac653bd28d54314a9e022bbc1a99',
     'metadata_url':REPOSITORY,
     'metadata_sha256':'27ada136bdae86b6a87d8c0a40901e78402c902626b94c919d21c6e7038aff0d'}


def fetch(url):
    request=urllib.request.Request(url,headers={'User-Agent':'VirtualDesktopOfflineBuild/1.0'})
    with urllib.request.urlopen(request,timeout=180) as response:return response.read()


def resolve_pin():
    raw=fetch(REPOSITORY)
    root=ET.fromstring(raw)
    package=next(p for p in root if p.tag.split('}')[-1]=='remotePackage' and p.attrib.get('path')=='platform-tools')
    revision=package.find('revision')
    version='.'.join(revision.findtext(key,'0') for key in ('major','minor','micro'))
    archive=next(a for a in package.find('archives') if a.findtext('host-os')=='windows')
    complete=archive.find('complete')
    return {'version':version,'url':'https://dl.google.com/android/repository/'+complete.findtext('url'),
            'sha1':complete.findtext('checksum'),'size':int(complete.findtext('size')),
            'metadata_url':REPOSITORY,'metadata_sha256':hashlib.sha256(raw).hexdigest()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resolve-pin',action='store_true',help='Maintainer-only: print current official SDK metadata without installing anything')
    args=parser.parse_args()
    if args.resolve_pin:
        print(json.dumps(resolve_pin(),indent=2));return
    if PIN is None:raise RuntimeError('The package maintainer must set a verified platform-tools pin')
    TOOLS.mkdir(parents=True,exist_ok=True)
    archive=TOOLS/('platform-tools-'+PIN['version']+'-windows.zip')
    def verified(data):
        if len(data)!=PIN['size'] or hashlib.sha1(data).hexdigest()!=PIN['sha1']:
            raise ValueError('Official platform-tools download does not match pinned SDK metadata')
        if PIN.get('sha256') and hashlib.sha256(data).hexdigest()!=PIN['sha256']:
            raise ValueError('Official platform-tools SHA-256 mismatch')
    if archive.exists():
        data=archive.read_bytes();verified(data)
    else:
        data=fetch(PIN['url']);verified(data)
        archive.write_bytes(data)
    with zipfile.ZipFile(archive) as source:
        if source.testzip() is not None:raise ValueError('Platform-tools ZIP integrity check failed')
        for entry in source.infolist():
            relative=PurePosixPath(entry.filename)
            if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0]!='platform-tools':
                raise ValueError('Unexpected path in official platform-tools ZIP')
            target=TOOLS.joinpath(*relative.parts)
            target.resolve().relative_to((TOOLS/'platform-tools').resolve())
            if entry.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(source.read(entry))
    destination=TOOLS/'platform-tools'
    required=('adb.exe','AdbWinApi.dll','AdbWinUsbApi.dll','source.properties','NOTICE.txt')
    if any(not (destination/name).is_file() for name in required):raise ValueError('Official platform-tools package is incomplete')
    properties=(destination/'source.properties').read_text()
    if 'Pkg.Revision='+PIN['version'] not in properties.replace(' ',''):
        raise ValueError('Platform-tools package version does not match its pin')
    files=[{'path':str(p.relative_to(destination)).replace('\\','/'),'size':p.stat().st_size,
            'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(destination.rglob('*')) if p.is_file() and p.name!='manifest.json']
    record={'schema_version':1,'version':PIN['version'],'source':PIN,'archive_sha256':hashlib.sha256(data).hexdigest(),
            'entrypoint':'adb.exe','files':files,'device_commands_executed':False}
    (destination/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'version':record['version'],'directory':str(destination),'files':len(files),
                      'archive_sha256':record['archive_sha256'],'device_commands_executed':False},indent=2))


if __name__=='__main__':main()
