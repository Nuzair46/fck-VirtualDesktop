"""ADB operations for the local VD patch toolchain.

No function runs at import time. Installation and data migration are explicit
calls. The runner is injectable so all destructive paths can be tested without
a headset. This module never disables Android package-signature enforcement.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import tarfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable

PACKAGE = 'VirtualDesktop.Android'
VERSION_NAME = '1.34.25.0'
VERSION_CODE = 10722
STOCK_SHA256 = '3b6ff43614e4af1c4aa70f6ff3e5ad3108c7f29fb8f1657a2a134e104ee050b9'
PRIVATE_ROOTS = ('data/user/0/' + PACKAGE, 'data/user_de/0/' + PACKAGE)
SHARED_ROOTS = ('data/media/0/Android/data/' + PACKAGE,
                'data/media/0/Android/obb/' + PACKAGE)
DATA_ROOTS = PRIVATE_ROOTS + SHARED_ROOTS
_HASH = re.compile(r'^[0-9a-f]{64}$')
_SERIAL = re.compile(r'^[A-Za-z0-9_.:\[\]%-]+$')
BUNDLED_ADB = Path(__file__).resolve().parent.parent / 'android-runtime/tools/platform-tools/adb.exe'


class DeviceError(RuntimeError): pass
class DeviceSelectionError(DeviceError): pass
class UnsupportedDeviceError(DeviceError): pass
class RootUnavailable(DeviceError): pass
class DataBackupError(DeviceError): pass
class InstallVerificationError(DeviceError): pass


def file_sha256(path: Path | str) -> str:
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _decode(value) -> str:
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else str(value or '')


def _write_json(path: Path, data: dict):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')
    temporary.replace(path)


class JsonRecord:
    def to_dict(self): return asdict(self)


@dataclass(frozen=True)
class AdbListing(JsonRecord):
    serial: str
    state: str
    model: str = ''


@dataclass(frozen=True)
class DeviceInfo(JsonRecord):
    serial: str
    hardware_serial: str
    model: str
    android_user: int
    installed: bool
    package: str = PACKAGE
    version_name: str | None = None
    version_code: int | None = None
    apk_paths: tuple[str, ...] = ()
    installed_sha256: str | None = None
    uid: int | None = None

    def require_supported(self):
        if not self.installed:
            raise UnsupportedDeviceError('Virtual Desktop is not installed on Android user 0.')
        if self.package != PACKAGE or self.android_user != 0:
            raise UnsupportedDeviceError('This operation requires VirtualDesktop.Android under Android user 0.')
        if not self.version_name or self.version_code is None or self.version_code < 1:
            raise UnsupportedDeviceError('Cannot establish installed Virtual Desktop version metadata.')
        if len(self.apk_paths) != 1:
            raise UnsupportedDeviceError('This patch supports one base APK; split APK installations are not supported.')
        if not self.installed_sha256 or not _HASH.fullmatch(self.installed_sha256):
            raise UnsupportedDeviceError('Cannot establish the installed APK hash.')
        return self


@dataclass(frozen=True)
class ApkCopy(JsonRecord):
    path: str
    sha256: str
    size: int
    device_serial: str
    remote_path: str
    version_name: str
    version_code: int


@dataclass(frozen=True)
class BackupRecord(JsonRecord):
    schema: int
    archive: str
    sha256: str
    size: int
    serial: str
    hardware_serial: str
    model: str
    package: str
    android_user: int
    source_apk_sha256: str
    version_name: str
    version_code: int
    roots: tuple[str, ...]
    members: int
    regular_bytes: int
    root_mode: str
    created_utc: str

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        value['roots'] = tuple(value['roots'])
        return cls(**value)


@dataclass(frozen=True)
class InstallResult(JsonRecord):
    success: bool
    status: str
    message: str = ''
    device: dict | None = None
    replaced: bool = False
    backup: dict | None = None
    data_restored: bool = False
    data_reset: bool = False

    @property
    def signature_mismatch(self):
        return self.status == 'signature_mismatch'


def discover_adb(explicit_path: str | Path | None = None) -> Path | None:
    """Return an existing adb executable; absence is valid for manual APK mode."""
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.is_file():
            raise DeviceError('The selected adb executable does not exist: ' + str(path))
        return path.resolve()
    if BUNDLED_ADB.is_file():
        directory = BUNDLED_ADB.parent
        try:
            manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8-sig'))
            records = {entry['path']: entry['sha256'] for entry in manifest['files']}
            if manifest.get('entrypoint') != 'adb.exe': raise ValueError('Unexpected ADB entrypoint')
            for name in ('adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll'):
                expected = records.get(name, '')
                if not _HASH.fullmatch(expected) or not (directory / name).is_file() or file_sha256(directory / name) != expected:
                    raise ValueError('Bundled ADB integrity mismatch: ' + name)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DeviceError('The bundled ADB files are incomplete or changed; restore the portable tool package, or select an explicit external adb.exe. ' + str(exc)) from exc
        return BUNDLED_ADB.resolve()
    candidates = []
    found = shutil.which('adb')
    if found: candidates.append(Path(found))
    for variable in ('ANDROID_SDK_ROOT', 'ANDROID_HOME'):
        if os.environ.get(variable): candidates.append(Path(os.environ[variable]) / 'platform-tools/adb.exe')
    local = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
    candidates.extend([
        local / 'Android/Sdk/platform-tools/adb.exe',
        local / 'QproFaceTracking.App/current/platform-tools/adb.exe',
        local / 'QproFaceTracking/app/platform-tools/adb.exe',
    ])
    return next((p.resolve() for p in candidates if p.is_file()), None)


def list_devices(adb: str | Path, *, runner: Callable = subprocess.run) -> list[AdbListing]:
    try:
        process = runner([str(adb), 'devices', '-l'], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeviceError('Cannot run adb devices: ' + str(exc)) from exc
    if process.returncode:
        raise DeviceError('adb devices failed: ' + _decode(process.stderr)[-1500:])
    result = []
    for line in _decode(process.stdout).splitlines():
        if not line.strip() or line.startswith(('List of devices', '*')): continue
        fields = line.split()
        if len(fields) < 2: continue
        serial, state = fields[:2]
        if not _SERIAL.fullmatch(serial): continue
        model = next((v[6:] for v in fields[2:] if v.startswith('model:')), '')
        result.append(AdbListing(serial, state, model))
    return result


def select_device(adb: str | Path, serial: str | None = None, *, runner: Callable = subprocess.run,
                  on_event: Callable | None = None) -> 'AdbDevice':
    devices = list_devices(adb, runner=runner)
    if serial:
        matches = [d for d in devices if d.serial == serial]
        if not matches: raise DeviceSelectionError('Selected headset is not listed by ADB: ' + serial)
        if matches[0].state != 'device':
            raise DeviceSelectionError('Selected headset is ' + matches[0].state + '; authorize/connect it before proceeding.')
    else:
        matches = [d for d in devices if d.state == 'device']
        if not matches:
            states = ', '.join(sorted({d.state for d in devices})) or 'none'
            raise DeviceSelectionError('No authorized ADB headset is available (states: ' + states + '). Manual APK mode remains available.')
        if len(matches) != 1:
            raise DeviceSelectionError('Multiple ADB devices are connected; select a serial explicitly.')
    return AdbDevice(adb, matches[0].serial, runner=runner, on_event=on_event)


def _archive_name(name: str) -> str:
    while name.startswith('./'): name = name[2:]
    name = name.rstrip('/')
    if not name or '\\' in name or name.startswith('/') or any(p in ('', '..') for p in name.split('/')):
        raise DataBackupError('Unsafe path in private-data archive: ' + repr(name))
    if not any(name == root or name.startswith(root + '/') for root in DATA_ROOTS):
        raise DataBackupError('Archive contains data outside this app: ' + repr(name))
    return name


def validate_backup_tar(path: str | Path) -> dict:
    """Read every regular file; reject traversal, duplicate paths and special files."""
    members = 0
    regular_bytes = 0
    seen = set()
    roots = set()
    try:
        size = Path(path).stat().st_size
        if size < 1024 or size % 512:
            raise DataBackupError('Private-data tar is truncated or has invalid block alignment')
        with Path(path).open('rb') as tail:
            tail.seek(-1024, 2)
            if tail.read() != b'\0' * 1024:
                raise DataBackupError('Private-data tar has no complete end-of-archive marker')
        with tarfile.open(path, 'r:') as archive:
            for entry in archive:
                name = _archive_name(entry.name)
                if name in seen: raise DataBackupError('Duplicate archive member: ' + name)
                seen.add(name)
                root = next(r for r in DATA_ROOTS if name == r or name.startswith(r + '/'))
                roots.add(root)
                if not (entry.isfile() or entry.isdir() or entry.issym() or entry.islnk()):
                    raise DataBackupError('Unsupported special file in backup: ' + name)
                if entry.isfile():
                    stream = archive.extractfile(entry)
                    consumed = 0
                    for block in iter(lambda: stream.read(1024 * 1024), b''): consumed += len(block)
                    if consumed != entry.size: raise DataBackupError('Truncated archive member: ' + name)
                    regular_bytes += consumed
                members += 1
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise DataBackupError('Private-data tar verification failed: ' + str(exc)) from exc
    if not members or DATA_ROOTS[0] not in roots:
        raise DataBackupError('Private-data backup does not contain credential-encrypted app data.')
    return {'roots': tuple(sorted(roots)), 'members': members, 'regular_bytes': regular_bytes}


class AdbDevice:
    def __init__(self, adb: str | Path, serial: str, *, runner: Callable = subprocess.run,
                 on_event: Callable | None = None):
        if not _SERIAL.fullmatch(serial): raise DeviceError('Invalid ADB serial')
        self.adb = str(adb)
        self.serial = serial
        self.runner = runner
        self.on_event = on_event
        self._bound_identity: tuple[str, str, int] | None = None

    def bind_identity(self, info: DeviceInfo | dict):
        """Bind subsequent mutating stages to an already inspected headset."""
        value = info.to_dict() if isinstance(info, DeviceInfo) else dict(info)
        if value.get('serial') != self.serial or not value.get('hardware_serial') or value.get('android_user') != 0:
            raise UnsupportedDeviceError('A stable owner-profile headset identity is required for mutation binding')
        self._bound_identity = (value['hardware_serial'], value['model'], value['android_user'])
        return self

    def _event(self, stage: str, **fields):
        if self.on_event: self.on_event(stage, fields)

    def _run(self, args: list[str], *, timeout: int = 60, stdout=None, stdin=None):
        try:
            return self.runner([self.adb, '-s', self.serial, *map(str, args)],
                               stdout=stdout if stdout is not None else subprocess.PIPE,
                               stderr=subprocess.PIPE, stdin=stdin, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DeviceError('ADB operation failed: ' + str(exc)) from exc

    def _shell(self, command: str, *, check=True, stdin=None, timeout=60):
        result = self._run(['shell', '-T', command], stdin=stdin, timeout=timeout)
        if check and result.returncode:
            raise DeviceError('ADB shell failed: ' + (_decode(result.stderr) + _decode(result.stdout))[-1500:])
        return result

    def _current_user(self) -> int:
        value = _decode(self._shell('am get-current-user').stdout).strip()
        if not value.isdecimal(): raise DeviceError('Cannot determine current Android user')
        return int(value)

    def _require_user0(self):
        user = self._current_user()
        if user != 0:
            raise UnsupportedDeviceError('App extraction and migration support Android user 0 only; switch to the headset owner profile.')
        if self._bound_identity is not None:
            hardware, model = self._identity()
            if (hardware, model, user) != self._bound_identity:
                raise UnsupportedDeviceError('The connected headset identity changed; refusing to modify a device at a reused ADB address')

    @staticmethod
    def _validate_apk_path(path: str) -> str:
        if not path.startswith('/data/app/') or not path.endswith('.apk') or '..' in path.split('/'):
            raise DeviceError('Unexpected installed APK path: ' + repr(path))
        if not re.fullmatch(r'/[A-Za-z0-9_./~=+%-]+', path):
            raise DeviceError('Unsupported characters in installed APK path')
        return path

    def _installed_paths(self) -> tuple[str, ...]:
        result = self._shell('pm path --user 0 ' + PACKAGE, check=False)
        output, error = _decode(result.stdout).strip(), _decode(result.stderr).strip()
        detail = '\n'.join(part for part in (output, error) if part)
        missing = not detail or re.search(r'unknown package|not found', detail, re.IGNORECASE)
        if result.returncode and not (result.returncode == 1 and missing):
            raise DeviceError(f'Cannot query installed APK paths (exit {result.returncode}): ' + (detail[-1500:] or 'no output'))
        paths = [self._validate_apk_path(line[8:].strip()) for line in output.splitlines() if line.startswith('package:')]
        if paths:
            if result.returncode:
                raise DeviceError('Package path query returned paths together with an error: ' + detail[-1500:])
            return tuple(paths)
        # Quest's package manager returns exit 1 with no output after a normal
        # uninstall. Confirm absence independently instead of treating that
        # result as a broken connection or blocking the rollback installer.
        if not missing:
            raise DeviceError('Unexpected package path response: ' + detail[-1500:])
        listed = self._shell('pm list packages --user 0 ' + PACKAGE, check=False)
        listing = _decode(listed.stdout).strip()
        listing_error = _decode(listed.stderr).strip()
        if listed.returncode:
            reason = '\n'.join(part for part in (listing, listing_error) if part)
            raise DeviceError(f'Cannot confirm package absence (exit {listed.returncode}): ' + (reason[-1500:] or 'no output'))
        rows = [line.strip() for line in listing.splitlines() if line.strip()]
        if any(not re.fullmatch(r'package:[A-Za-z0-9_.]+', line) for line in rows):
            raise DeviceError('Unexpected package inventory response: ' + listing[-1500:])
        if 'package:' + PACKAGE in rows:
            raise DeviceError('Virtual Desktop is listed as installed, but Android returned no APK path. Reconnect the headset and retry.')
        return ()

    def _remote_sha256(self, path: str) -> str:
        self._validate_apk_path(path)
        output = _decode(self._shell('sha256sum ' + shlex.quote(path)).stdout).strip()
        parts = output.split(None, 1)
        if len(parts) != 2 or not _HASH.fullmatch(parts[0].lower()):
            raise DeviceError('Headset did not return a valid APK SHA-256 digest')
        if parts[1].lstrip('*').strip() != path:
            raise DeviceError('Headset SHA-256 result refers to a different file')
        return parts[0].lower()

    def _identity(self) -> tuple[str, str]:
        model = _decode(self._shell('getprop ro.product.model').stdout).strip()
        hardware = _decode(self._shell('getprop ro.serialno').stdout).strip()
        if not hardware:
            hardware = _decode(self._shell('getprop ro.boot.serialno').stdout).strip()
        return hardware, model

    def inspect(self, *, require_user0=True) -> DeviceInfo:
        user = self._current_user()
        if require_user0 and user != 0:
            raise UnsupportedDeviceError('This tool supports Android user 0 only; switch to the headset owner profile.')
        hardware, model = self._identity()
        output = _decode(self._shell('dumpsys package ' + PACKAGE).stdout)
        paths = self._installed_paths()
        name = re.search(r'^\s*versionName=(\S+)\s*$', output, re.MULTILINE)
        code = re.search(r'\bversionCode=(\d+)\b', output)
        dump_uids = {int(value) for value in re.findall(r'^\s*(?:userId|appId)=(\d+)(?:\s|$)', output, re.MULTILINE)}
        if len(dump_uids) > 1:
            raise DeviceError('Package dump contains inconsistent app UID values')
        uid = next(iter(dump_uids), None) if paths else None
        if paths:
            # Modern Quest releases use appId= in dumpsys; older Android uses
            # userId=. Ask the package manager for this exact package under user
            # 0 as an independent check, excluding similarly named packages.
            package_uid = self._shell('pm list packages -U --user 0 ' + PACKAGE, check=False)
            if package_uid.returncode == 0:
                rows = re.findall(r'^\s*package:' + re.escape(PACKAGE) + r'\s+uid:(\d+)\s*$',
                                  _decode(package_uid.stdout), re.MULTILINE)
                if len(rows) != 1:
                    raise DeviceError('Package manager did not return exactly one UID for Virtual Desktop')
                exact_uid = int(rows[0])
                if uid is not None and uid != exact_uid:
                    raise DeviceError('Package-manager UID does not match the package dump')
                uid = exact_uid
        digest = self._remote_sha256(paths[0]) if len(paths) == 1 else None
        return DeviceInfo(serial=self.serial, hardware_serial=hardware, model=model,
                          android_user=user, installed=bool(paths), package=PACKAGE,
                          version_name=name.group(1) if name else None,
                          version_code=int(code.group(1)) if code else None,
                          apk_paths=paths, installed_sha256=digest,
                          uid=uid)

    def installed_sha256(self) -> str:
        self._require_user0()
        paths = self._installed_paths()
        if len(paths) != 1: raise UnsupportedDeviceError('Expected exactly one installed base APK')
        return self._remote_sha256(paths[0])

    def pull_original(self, destination: str | Path, *, expected_sha256=None) -> ApkCopy:
        info = self.inspect().require_supported()
        expected_sha256 = expected_sha256 or info.installed_sha256
        if info.installed_sha256 != expected_sha256:
            raise UnsupportedDeviceError('Installed APK changed after source selection; prepare again from the current input.')
        destination = Path(destination).resolve()
        temporary = destination.with_suffix(destination.suffix + '.partial')
        if destination.exists() or temporary.exists(): raise DeviceError('APK extraction destination already exists')
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._event('apk_pull_started', destination=str(destination))
        result = self._run(['pull', info.apk_paths[0], str(temporary)], timeout=1200)
        if result.returncode or not temporary.is_file():
            raise DeviceError('ADB pull failed: ' + _decode(result.stderr)[-1500:])
        digest = file_sha256(temporary)
        remote_after = self._remote_sha256(info.apk_paths[0])
        if digest != expected_sha256 or digest != remote_after:
            raise DeviceError('APK copy verification failed; the partial file was retained for inspection')
        temporary.replace(destination)
        record = ApkCopy(str(destination), digest, destination.stat().st_size, self.serial,
                         info.apk_paths[0], info.version_name, info.version_code)
        self._event('apk_pull_verified', **record.to_dict())
        return record

    def root_mode(self) -> str:
        direct = self._shell('id -u', check=False)
        if direct.returncode == 0 and _decode(direct.stdout).strip() == '0': return 'adbd'
        elevated = self._shell("su -c 'id -u'", check=False)
        if elevated.returncode == 0 and _decode(elevated.stdout).strip() == '0': return 'su'
        raise RootUnavailable('Root access is unavailable. A private-data backup cannot be made; clean reinstall requires a separate explicit data-loss opt-in.')

    @staticmethod
    def _root_command(mode: str, command: str) -> str:
        if mode == 'adbd': return command
        if mode == 'su': return 'su -c ' + shlex.quote(command)
        raise DeviceError('Invalid root mode')

    def backup_private_data(self, backup_directory: str | Path) -> BackupRecord:
        self._require_user0()
        info = self.inspect().require_supported()
        mode = self.root_mode()
        if not info.hardware_serial:
            raise DataBackupError('Cannot bind a private-data backup to a stable headset hardware serial')
        directory = Path(backup_directory).resolve()
        archive = directory / 'private-data.tar'
        temporary = directory / 'private-data.tar.partial'
        metadata = directory / 'private-data.json'
        if any(p.exists() for p in (archive, temporary, metadata)):
            raise DataBackupError('Backup directory already contains a backup or partial backup')
        directory.mkdir(parents=True, exist_ok=True)
        roots = []
        for root in DATA_ROOTS:
            probe = self._shell(self._root_command(mode, 'test -d /' + root), check=False)
            if probe.returncode == 0: roots.append(root)
        if DATA_ROOTS[0] not in roots: raise DataBackupError('Primary private app-data directory is unavailable')
        self._event('private_backup_started', directory=str(directory), root_mode=mode)
        self._require_user0()
        self._shell('am force-stop --user 0 ' + PACKAGE)
        command = 'tar -C / -cf - ' + ' '.join(roots)
        with temporary.open('xb') as stream:
            result = self._run(['exec-out', self._root_command(mode, command)], stdout=stream, timeout=1200)
        if result.returncode:
            raise DataBackupError('Root tar backup failed; the partial archive was retained: ' + _decode(result.stderr)[-1500:])
        summary = validate_backup_tar(temporary)
        if set(summary['roots']) != set(roots): raise DataBackupError('Backup root-directory coverage does not match request')
        digest = file_sha256(temporary)
        if self.installed_sha256() != info.installed_sha256:
            raise DataBackupError('Installed APK changed while private data was being backed up')
        temporary.replace(archive)
        record = BackupRecord(1, str(archive), digest, archive.stat().st_size, self.serial,
                              info.hardware_serial, info.model, PACKAGE, 0,
                              info.installed_sha256, info.version_name, info.version_code,
                              summary['roots'], summary['members'], summary['regular_bytes'], mode,
                              datetime.now(timezone.utc).isoformat())
        _write_json(metadata, record.to_dict())
        self._event('private_backup_verified', **record.to_dict())
        return record

    def _verify_backup(self, record: BackupRecord | dict) -> BackupRecord:
        if isinstance(record, dict): record = BackupRecord.from_dict(record)
        if (record.schema, record.serial, record.package, record.android_user) != (1, self.serial, PACKAGE, 0):
            raise DataBackupError('Backup belongs to a different device, app, user, or schema')
        hardware, model = self._identity()
        if not record.hardware_serial or (hardware, model) != (record.hardware_serial, record.model):
            raise DataBackupError('Connected headset hardware identity does not match the backup; an ADB address may have been reused')
        path = Path(record.archive)
        if not path.is_file() or path.stat().st_size != record.size or file_sha256(path) != record.sha256:
            raise DataBackupError('Backup file integrity verification failed')
        summary = validate_backup_tar(path)
        if (tuple(summary['roots']), summary['members'], summary['regular_bytes']) != (tuple(record.roots), record.members, record.regular_bytes):
            raise DataBackupError('Backup manifest does not match its archive')
        return record

    def restore_private_data(self, record: BackupRecord | dict) -> dict:
        self._require_user0()
        record = self._verify_backup(record)
        info = self.inspect().require_supported()
        if info.uid is None or not 10000 <= info.uid < 100000:
            raise DataBackupError('Cannot establish the newly installed app UID')
        mode = self.root_mode()
        restored = Path(record.archive).with_name('private-data.restore-' + uuid.uuid4().hex + '.tar')
        skipped = []
        # Strip links and regenerated runtime caches before invoking privileged
        # tar. Root extraction never follows a link supplied by this archive.
        with tarfile.open(record.archive, 'r:') as source, restored.open('xb') as staged, tarfile.open(fileobj=staged, mode='w:') as target:
            for entry in source:
                name = _archive_name(entry.name)
                root = next(r for r in DATA_ROOTS if name == r or name.startswith(r + '/'))
                relative = name[len(root):].lstrip('/')
                first = relative.split('/', 1)[0] if relative else ''
                if entry.issym() or entry.islnk() or (root in PRIVATE_ROOTS and first in ('lib', 'cache', 'code_cache')):
                    skipped.append(name)
                    continue
                replacement = copy.copy(entry)
                replacement.name = name
                if root in PRIVATE_ROOTS:
                    replacement.uid = replacement.gid = info.uid
                replacement.uname = replacement.gname = ''
                replacement.mode &= 0o777
                replacement.pax_headers = {}
                target.addfile(replacement, source.extractfile(entry) if entry.isfile() else None)
        validate_backup_tar(restored)
        remote_archive = '/data/local/tmp/vd-offline-restore-' + uuid.uuid4().hex + '.tar'
        self._event('private_restore_started', archive=record.archive, new_uid=info.uid,
                    restore_staging_path=str(restored), remote_staging_path=remote_archive)
        self._require_user0()
        if self._shell('test ! -e ' + shlex.quote(remote_archive), check=False).returncode:
            raise DataBackupError('The unique remote restore staging path already exists')
        pushed = self._run(['push', str(restored), remote_archive], timeout=300)
        if pushed.returncode:
            raise DataBackupError('Could not transfer the restore archive: ' +
                                  (_decode(pushed.stdout) + _decode(pushed.stderr))[-1500:])
        self._shell(self._root_command(mode, 'chmod 600 ' + shlex.quote(remote_archive)))
        transferred = _decode(self._shell(self._root_command(mode,
                              'sha256sum ' + shlex.quote(remote_archive))).stdout).strip().split(None, 1)
        if len(transferred) != 2 or transferred[0].lower() != file_sha256(restored) or transferred[1].strip() != remote_archive:
            raise DataBackupError('The transferred restore archive failed verification; app data was not cleared')
        self._require_user0()
        self._shell('am force-stop --user 0 ' + PACKAGE)
        cleared = self._shell('pm clear --user 0 ' + PACKAGE)
        if _decode(cleared.stdout).strip() != 'Success':
            raise DataBackupError('Android did not confirm clearing the destination app data before restore')
        roots = ['/' + r for r in record.roots]
        private_roots = ['/' + r for r in record.roots if r in PRIVATE_ROOTS]
        # A file is seekable and avoids device-specific tar/su handling of ADB
        # shell stdin. Verify transfer before clearing any current app data.
        command = 'tar -C / -xf {0} && chown -R {1}:{1} {2} && restorecon -RF {3}'.format(
            shlex.quote(remote_archive), info.uid, ' '.join(private_roots), ' '.join(roots))
        self._shell(self._root_command(mode, command), timeout=1200)
        ownership = _decode(self._shell(self._root_command(mode, 'stat -c %u:%g ' + ' '.join(private_roots))).stdout).splitlines()
        if ownership != [f'{info.uid}:{info.uid}'] * len(private_roots):
            raise DataBackupError('Restored app-data ownership could not be verified')
        self._shell(self._root_command(mode, 'rm -f -- ' + shlex.quote(remote_archive)))
        # The sanitized staging file is not needed after a successful restore.
        # Its exact location was constructed inside the validated backup folder.
        restored.unlink()
        result = {'restored': True, 'uid': info.uid, 'skipped_links_and_caches': skipped,
                  'shared_storage_ownership_preserved': True,
                  'archive_sha256': record.sha256, 'application_launch_tested': False}
        self._event('private_restore_completed', **result)
        return result

    def install(self, apk: str | Path, expected_sha256: str) -> InstallResult:
        self._require_user0()
        apk = Path(apk).resolve()
        if not _HASH.fullmatch(expected_sha256) or file_sha256(apk) != expected_sha256:
            raise DeviceError('APK installation input SHA-256 mismatch')
        self._event('apk_install_started', path=str(apk), sha256=expected_sha256)
        self._require_user0()
        result = self._run(['install', '-r', '--user', '0', str(apk)], timeout=1200)
        output = (_decode(result.stdout) + '\n' + _decode(result.stderr)).strip()
        if result.returncode or re.search(r'\bFailure\s*\[', output):
            mismatch = bool(re.search(r'\bINSTALL_FAILED_UPDATE_INCOMPATIBLE\b', output))
            status = 'signature_mismatch' if mismatch else 'install_failed'
            self._event('apk_install_failed', status=status, message=output[-2000:])
            return InstallResult(False, status, output[-2000:])
        if not re.search(r'^Success\s*$', output, re.MULTILINE):
            raise InstallVerificationError('ADB did not explicitly confirm a successful installation')
        info = self.inspect().require_supported()
        if info.installed_sha256 != expected_sha256:
            raise InstallVerificationError('Installed headset APK does not match the requested output hash')
        self._event('apk_install_verified', **info.to_dict())
        return InstallResult(True, 'installed', device=info.to_dict())

    def _assert_no_other_users(self):
        users = _decode(self._shell('pm list users').stdout)
        ids = [int(v) for v in re.findall(r'UserInfo\{(\d+):', users)]
        if not ids or 0 not in ids: raise DeviceError('Cannot verify Android user inventory before package removal')
        for user in ids:
            if user == 0: continue
            output = _decode(self._shell(f'pm list packages --user {user} ' + PACKAGE).stdout)
            if 'package:' + PACKAGE in output.splitlines():
                raise UnsupportedDeviceError('Virtual Desktop is also installed for another Android user; automatic package replacement is refused.')

    def _uninstall(self):
        self._require_user0()
        self._assert_no_other_users()
        self._event('apk_uninstall_started', package=PACKAGE)
        self._require_user0()
        result = self._run(['uninstall', PACKAGE], timeout=300)
        output = (_decode(result.stdout) + '\n' + _decode(result.stderr)).strip()
        if result.returncode or not re.search(r'^Success\s*$', output, re.MULTILINE):
            raise DeviceError('Package removal failed: ' + output[-1500:])
        if self._installed_paths(): raise DeviceError('Package remains installed after reported uninstall')
        self._event('apk_uninstall_completed', package=PACKAGE)

    def replacement_install(self, apk: str | Path, expected_sha256: str, backup_directory: str | Path,
                            *, allow_destructive_reset=False, allow_data_loss=False,
                            backup_record: BackupRecord | dict | None = None,
                            stock_apk: str | Path | None = None, expected_stock_sha256: str | None = None,
                            restore_data=True) -> InstallResult:
        """Try an update; only an exact signature-mismatch result permits removal.

        The caller must obtain operator consent before setting destructive flags.
        A verified backup is required unless root is unavailable AND the caller
        separately opted into losing data. Failures after uninstall are surfaced
        for the toolchain journal; rollback() is an explicit recovery operation.
        """
        result = self.install(apk, expected_sha256)
        if result.success or not result.signature_mismatch or not allow_destructive_reset: return result
        info = self.inspect().require_supported()
        if stock_apk is None or not expected_stock_sha256 or not _HASH.fullmatch(expected_stock_sha256):
            raise DeviceError('A saved original APK and its prepared-session SHA-256 are required before replacement, for rollback')
        if file_sha256(stock_apk) != expected_stock_sha256:
            raise DeviceError('Original recovery APK does not match its prepared-session SHA-256')
        if info.installed_sha256 != expected_stock_sha256:
            raise DeviceError('Currently installed APK differs from the saved original selected for this session')
        if not info.hardware_serial:
            raise UnsupportedDeviceError('Cannot establish a stable headset identity before package replacement')
        record = self._verify_backup(backup_record) if backup_record else None
        if record is not None and record.source_apk_sha256 != info.installed_sha256:
            raise DataBackupError('Backup does not belong to the currently installed APK')
        if record is None:
            try:
                record = self.backup_private_data(backup_directory)
            except RootUnavailable:
                if not allow_data_loss: raise
                self._event('private_backup_unavailable_data_loss_accepted', package=PACKAGE)
        # Recheck the new APK after a potentially lengthy backup and before the
        # irreversible operation. The original recovery APK is rechecked too.
        if file_sha256(apk) != expected_sha256 or file_sha256(stock_apk) != expected_stock_sha256:
            raise DeviceError('Installation/recovery input changed during backup')
        if self._identity() != (info.hardware_serial, info.model):
            raise UnsupportedDeviceError('Headset hardware changed during backup; package removal was not started')
        if self.installed_sha256() != info.installed_sha256:
            raise UnsupportedDeviceError('Installed headset APK changed during backup; package removal was not started')
        self._event('replacement_prepared', backup=record.to_dict() if record else None,
                    recovery_apk=str(Path(stock_apk).resolve()), recovery_apk_sha256=expected_stock_sha256,
                    allow_data_loss=bool(allow_data_loss))
        self._uninstall()
        installed = self.install(apk, expected_sha256)
        if not installed.success:
            self._event('replacement_needs_rollback', message=installed.message)
            return InstallResult(False, 'replacement_failed', installed.message, replaced=True,
                                 backup=record.to_dict() if record else None, data_reset=record is None)
        restored = False
        if record is not None and restore_data:
            self.restore_private_data(record)
            restored = True
        return InstallResult(True, 'replaced', device=installed.device, replaced=True,
                             backup=record.to_dict() if record else None,
                             data_restored=restored, data_reset=record is None)

    def rollback(self, stock_apk: str | Path, backup_record: BackupRecord | dict | None = None,
                 *, expected_stock_sha256: str | None = None,
                 allow_destructive_reset=False, allow_data_loss=False) -> InstallResult:
        """Explicitly restore the saved stock APK, then its verified private data."""
        self._require_user0()
        record = self._verify_backup(backup_record) if backup_record else None
        expected_stock_sha256 = expected_stock_sha256 or (record.source_apk_sha256 if record else None)
        if not expected_stock_sha256 or not _HASH.fullmatch(expected_stock_sha256):
            raise DeviceError('Recovery requires the original APK SHA-256 recorded by the prepared session or verified backup')
        if file_sha256(stock_apk) != expected_stock_sha256:
            raise DeviceError('Recovery APK does not match the session original SHA-256')
        if record is not None and record.source_apk_sha256 != expected_stock_sha256:
            raise DataBackupError('Private-data backup belongs to a different original APK')
        result = self.install(stock_apk, expected_stock_sha256)
        if not result.success:
            if not result.signature_mismatch or not allow_destructive_reset: return result
            if record is None and not allow_data_loss:
                raise DataBackupError('Recovery needs a valid private-data backup or explicit data-loss consent before removing the current app')
            self._uninstall()
            result = self.install(stock_apk, expected_stock_sha256)
            if not result.success: return result
        if record is not None: self.restore_private_data(record)
        self._event('rollback_completed', restored_private_data=record is not None)
        return InstallResult(True, 'rolled_back', device=result.device, data_restored=record is not None,
                             data_reset=record is None, backup=record.to_dict() if record else None)
