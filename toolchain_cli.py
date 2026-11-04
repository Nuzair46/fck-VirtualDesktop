"""Detect, prepare, install and restore a local Virtual Desktop offline pair.

prepare performs no installed-app mutations. install/restore require an explicit
command, Windows elevation, an intact session, and device-migration consent.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from toolchain import device

ROOT = Path(__file__).resolve().parent
VERSION = '1.34.25.0'
APK_HASH = device.STOCK_SHA256
PC_HASH = '0229a322137f55687240d2b51f5f7002056d9277d7fa001ec6cb4eb25af0bfb8'
APK_NAME = 'VirtualDesktop-local.apk'
PC_NAME = 'VirtualDesktop.Streamer.exe'

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temp.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)

def default_state():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'VirtualDesktopOffline'

def child_environment():
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    runtime = ROOT / 'runtime/dotnet'
    if (runtime / 'dotnet.exe').exists():
        env['PATH'] = str(runtime) + os.pathsep + env.get('PATH', '')
        env['DOTNET_ROOT'] = str(runtime)
    return env

def run(args, *, capture=False):
    result = subprocess.run([str(x) for x in args], env=child_environment(),
        stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None,
        text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        detail = ((result.stderr or '') + '\n' + (result.stdout or '')).strip()[-4000:]
        raise RuntimeError('Command failed: ' + str(args[0]) + ('\n' + detail if detail else ''))
    return result.stdout if capture else None

def host(action, directory=None, **options):
    args = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            ROOT / 'toolchain/StreamerHost.ps1', '-Action', action]
    if directory:
        args += ['-InstallDirectory', directory]
    for key, value in options.items():
        if value is True:
            args.append('-' + key)
        elif value is not None and value is not False:
            args += ['-' + key, value]
    output = run(args, capture=True)
    try:
        return json.loads(output.lstrip('\ufeff'))
    except json.JSONDecodeError as exc:
        raise RuntimeError('Streamer host helper returned invalid JSON') from exc

def install_directory(executable):
    if not executable:
        return None
    path = Path(executable).expanduser().resolve(strict=True)
    if not path.is_file() or path.name.lower() != PC_NAME.lower():
        raise ValueError('Select the installed VirtualDesktop.Streamer.exe')
    return path.parent

def require_stock(info):
    if not info.get('Found') or info.get('IsAmbiguous'):
        raise ValueError('Select one installed VirtualDesktop.Streamer.exe; automatic detection found zero or multiple installations.')
    if not info.get('Version') or not re.fullmatch('[0-9a-f]{64}', info.get('Sha256', '').lower()):
        raise ValueError('Cannot identify the selected Streamer version and source hash')
    if info.get('SupportedPatched'):
        raise ValueError('Restore the existing Streamer patch before preparing another session')

@contextmanager
def operation_lock(base=None):
    # One local mutating operation at a time, even if several UI windows exist.
    directory = Path(base or default_state())
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'toolchain.lock'
    with path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        import msvcrt
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('Another toolchain operation is already running') from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

class Session:
    def __init__(self, directory, data):
        self.directory = Path(directory).resolve()
        self.data = data

    @classmethod
    def create(cls, state_directory, mode):
        identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
        directory = Path(state_directory).resolve() / 'sessions' / identifier
        directory.mkdir(parents=True)
        value = cls(directory, {'schema_version': 2, 'id': identifier, 'state': 'preparing',
            'mode': mode, 'created_utc': datetime.now(timezone.utc).isoformat(),
            'events': [], 'installation': {}})
        value.save()
        return value

    @classmethod
    def load(cls, path):
        path = Path(path).expanduser().resolve(strict=True)
        if path.is_file():
            path = path.parent
        data = json.loads((path / 'session.json').read_text(encoding='utf-8'))
        if data.get('schema_version') != 2:
            raise ValueError('Unsupported toolchain session')
        if data.get('mode') not in ('manual', 'adb'):
            raise ValueError('Invalid toolchain session mode')
        return cls(path, data)

    def save(self):
        atomic_json(self.directory / 'session.json', self.data)

    def event(self, stage, fields=None, **kwargs):
        details = dict(fields or {})
        details.update(kwargs)
        if stage == 'private_backup_verified':
            self.data['last_device_backup'] = details
            if self.data['state'] == 'installing':
                self.data['device_backup'] = details
                self.data.setdefault('original_device_backup', details)
        if stage == 'replacement_prepared' and details.get('backup'):
            self.data['device_backup'] = details['backup']
        if stage == 'apk_uninstall_started':
            self.data['installation']['device_mutation_started'] = True
        if stage == 'apk_install_verified':
            self.data['installation']['last_device_hash'] = details.get('installed_sha256')
        self.data['events'].append({'utc': datetime.now(timezone.utc).isoformat(), 'stage': stage, 'details': details})
        self.save()
        print(stage.replace('_', ' '), flush=True)

    def state(self, value, **fields):
        self.data['state'] = value
        self.data.update(fields)
        self.save()

    @property
    def originals(self): return self.directory / 'originals'
    @property
    def output(self): return self.directory / 'output'
    @property
    def settings(self): return Path(self.data.get('settings_state', self.directory / 'streamer-state'))
    @property
    def processes(self): return self.directory / 'streamer-process'
    @property
    def updater(self): return self.directory / 'streamer-updater'
    @property
    def install_dir(self): return Path(self.data['streamer']['InstallDirectory'])
    @property
    def original_pc_hash(self): return self.data['input_hashes'][PC_NAME]
    @property
    def original_apk_hash(self): return self.data['input_hashes']['base.apk']

    def validate_artifacts(self):
        self.validate_recovery()
        if (self.output / 'BUILD-INCOMPLETE.txt').exists():
            raise ValueError('Patch build is incomplete')
        report_path = self.output / 'build-report.json'
        if sha(report_path) != self.data.get('build_report_sha256'):
            raise ValueError('Prepared build report changed after preparation')
        report = json.loads(report_path.read_text())
        for name, expected in report['outputs'].items():
            path = (self.output / name).resolve()
            path.relative_to(self.output.resolve())
            if sha(path) != expected:
                raise ValueError('Prepared output failed verification: ' + name)
        if (report['inputs']['apk']['sha256'] != self.original_apk_hash or
            report['inputs']['streamer']['sha256'] != self.original_pc_hash or
            report.get('compatibility', {}).get('matched') is not True):
            raise ValueError('Prepared build does not match the saved originals and validated profiles')
        if self.data.get('output_hashes') != report['outputs']:
            raise ValueError('Prepared output manifest changed after preparation')
        return report

    def validate_recovery(self):
        # Recovery depends on intact saved originals, not disposable build files.
        for name in (PC_NAME, 'base.apk'):
            expected = self.data.get('input_hashes', {}).get(name, '')
            if not re.fullmatch('[0-9a-f]{64}', expected):
                raise ValueError('Recovery journal lacks an original file hash: ' + name)
            if sha(self.originals / name) != expected:
                raise ValueError('Original recovery file failed verification: ' + name)
        hashes = self.data.get('output_hashes', {})
        if any(not re.fullmatch('[0-9a-f]{64}', hashes.get(name, '')) for name in (PC_NAME, APK_NAME)):
            raise ValueError('Recovery journal lacks the expected patched file hashes')
        return hashes

def get_device(args, on_event=None):
    adb = device.discover_adb(args.adb)
    if adb is None:
        raise RuntimeError('ADB was not found. Select a manual APK, or supply the platform-tools adb.exe.')
    return device.select_device(adb, args.serial, on_event=on_event)

def session_device(session):
    expected = session.data['device']
    adb = device.discover_adb(session.data['adb_path'])
    target = device.select_device(adb, expected['serial'], on_event=session.event)
    actual = target.inspect()
    if not expected['hardware_serial'] or (actual.hardware_serial, actual.model, actual.android_user) != (
        expected['hardware_serial'], expected['model'], expected['android_user']):
        raise RuntimeError('The connected headset does not match this prepared session')
    target.bind_identity(expected)
    return target, actual

def scan(args):
    result = {'baseline_version': VERSION, 'manual_apk_supported': True,
              'compatibility': 'Code profiles are validated during Prepare; version and file hash alone do not establish compatibility.'}
    try:
        pc = host('Detect', install_directory(args.streamer))
        result['streamer'] = pc
        result['streamer_known_baseline'] = pc.get('Sha256', '').lower() == PC_HASH
    except Exception as exc:
        result['streamer_error'] = str(exc)
    if args.apk:
        path = Path(args.apk).resolve(strict=True)
        result['mode'] = 'manual'
        result['apk'] = {'path': str(path), 'sha256': sha(path)}
        result['apk']['known_baseline'] = result['apk']['sha256'] == APK_HASH
    else:
        result['mode'] = 'adb'
        try:
            adb = device.discover_adb(args.adb)
            result['adb_path'] = str(adb) if adb else None
            result['devices'] = [item.to_dict() for item in device.list_devices(adb)] if adb else []
            if adb:
                result['headset'] = device.select_device(adb, args.serial).inspect().to_dict()
        except Exception as exc:
            result['adb_error'] = str(exc)
    print(json.dumps(result, indent=2), flush=True)
    return result

def prepare(args):
    pc = host('Detect', install_directory(args.streamer))
    require_stock(pc)
    workspace = Path(args.state_directory or default_state()).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(workspace).free < 6 * 1024**3:
        raise RuntimeError('At least 6 GiB of free workspace is needed to extract and build this APK')
    mode = 'manual' if args.apk else 'adb'
    target = None
    if mode == 'manual':
        apk_input = Path(args.apk).expanduser().resolve(strict=True)
        apk_hash = sha(apk_input)
    else:
        target = get_device(args)
        info = target.inspect().require_supported()
        apk_hash = info.installed_sha256
        if not apk_hash or not re.fullmatch('[0-9a-f]{64}', apk_hash):
            raise ValueError('Cannot verify the original headset APK hash')
        if not info.hardware_serial:
            raise ValueError('Cannot establish a stable headset serial for installation/restore')
    session = Session.create(workspace, mode)
    print('SESSION_PATH=' + str(session.directory), flush=True)
    try:
        session.data['streamer'] = pc
        session.data['version'] = pc['Version']
        session.data['input_hashes'] = {PC_NAME: pc['Sha256'].lower(), 'base.apk': apk_hash}
        session.originals.mkdir()
        shutil.copy2(pc['Executable'], session.originals / PC_NAME)
        if sha(session.originals / PC_NAME) != session.original_pc_hash:
            raise ValueError('Installed Streamer changed during backup')
        if target:
            target.on_event = session.event
            session.data['device'] = info.to_dict()
            session.data['adb_path'] = str(target.adb)
            session.save()
            copied = target.pull_original(session.originals / 'base.apk', expected_sha256=apk_hash)
            session.data['apk_copy'] = copied.to_dict()
        else:
            shutil.copy2(apk_input, session.originals / 'base.apk')
            if sha(session.originals / 'base.apk') != apk_hash:
                raise ValueError('Manual APK changed during backup')
        session.event('originals_verified')
        identity = workspace / 'identity'
        run([sys.executable, ROOT / 'build.py', '--apk', session.originals / 'base.apk',
            '--streamer', session.originals / PC_NAME, '--output', session.output,
            '--pairing', identity / 'pairing.json', '--signing-directory', identity / 'apk-signing'])
        report = json.loads((session.output / 'build-report.json').read_text())
        session.data['output_hashes'] = report['outputs']
        session.data['build_report_sha256'] = sha(session.output / 'build-report.json')
        session.data['compatibility'] = report['compatibility']
        session.data['apk_version'] = report['inputs']['apk'].get('version')
        session.data['account'] = report['account']
        session.data['settings_stage'] = host('StageSettings', session.install_dir,
                                            StateDirectory=session.settings, Account=report['account'])
        session.validate_artifacts()
        session.state('prepared')
        session.event('ready_for_installation', mode=mode, account=report['account'])
        print('SESSION_PATH=' + str(session.directory), flush=True)
        print('Prepared. Installed applications are unchanged.', flush=True)
        if mode == 'manual':
            print('Manual mode: the headset will not be modified. APK for sideloading: ' + str(session.output / APK_NAME), flush=True)
    except Exception as exc:
        session.state('prepare_failed', error=str(exc))
        raise
    return session

def require_admin():
    if os.name != 'nt' or not ctypes.windll.shell32.IsUserAnAdmin():
        raise PermissionError('Install and Restore require Windows administrator rights. Relaunch the tool as administrator.')

def pc_installer(session, action):
    args = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            ROOT / 'streamer/Install-StreamerPatch.ps1', '-Action', action,
            '-InstallDirectory', session.install_dir]
    if action == 'Install':
        args += ['-PatchedExe', session.output / PC_NAME]
    return json.loads(run(args, capture=True).lstrip('\ufeff'))

def recover_after_failure(session, target, args):
    errors = []
    # A crash may happen after the atomic file replace but before its journal
    # update. Inspect exact current bytes instead of trusting only a stage flag.
    try:
        current = sha(session.install_dir / PC_NAME)
        patched = session.data['output_hashes'][PC_NAME]
        if current == patched:
            pc_installer(session, 'Restore')
        elif current != session.original_pc_hash:
            raise ValueError('Streamer changed to an unknown binary; automatic overwrite refused')
        if session.data['installation'].get('settings_apply_started'):
            host('RestoreSettings', session.install_dir, StateDirectory=session.settings)
    except Exception as exc:
        errors.append('PC recovery: ' + str(exc))
    if session.data['installation'].get('disable_updater'):
        try:
            host('RestoreUpdater', session.install_dir, StateDirectory=session.updater)
        except Exception as exc:
            errors.append('Updater recovery: ' + str(exc))
    if target:
        try:
            target, actual = session_device(session)
            patched_apk = session.data['output_hashes'][APK_NAME]
            if actual.installed_sha256 not in (None, session.original_apk_hash, patched_apk):
                raise RuntimeError('Headset changed to an unknown APK; automatic overwrite refused')
            if session.data['installation'].get('device_mutation_started') or actual.installed_sha256 == patched_apk:
                result = target.rollback(session.originals / 'base.apk', session.data.get('device_backup'),
                    allow_destructive_reset=args.confirm_reinstall, allow_data_loss=args.allow_data_loss,
                    expected_stock_sha256=session.original_apk_hash)
                if not result.success:
                    raise RuntimeError(result.message)
                session.event('headset_recovered', result.to_dict())
        except Exception as exc:
            errors.append('Headset recovery: ' + str(exc))
    session.state('recovery_required' if errors else 'rolled_back', recovery_errors=errors)
    if not errors:
        try:
            host('Start', session.install_dir, StateDirectory=session.processes)
        except Exception as exc:
            session.event('recovered_streamer_start_failed', message=str(exc))
    for error in errors:
        print(error, file=sys.stderr, flush=True)

def install(args):
    require_admin()
    session = Session.load(args.session)
    if session.data['state'] != 'prepared':
        raise RuntimeError('Only a prepared session can be installed. Current state: ' + session.data['state'])
    report = session.validate_artifacts()
    detected = host('Detect', session.install_dir)
    require_stock(detected)
    if detected['Sha256'].lower() != session.original_pc_hash:
        raise RuntimeError('The installed Streamer changed after preparation. Installation was not started.')
    target = None
    if session.data['mode'] == 'adb':
        if not args.confirm_reinstall:
            raise RuntimeError('Headset installation can require removing the stock app because its signing key changes. Confirm replacement first; no app has been changed.')
        target, info = session_device(session)
        if info.installed_sha256 != session.original_apk_hash:
            raise RuntimeError('The headset app changed after preparation. Installation was not started.')
    disable_updater = bool(getattr(args, 'disable_updater', False))
    if disable_updater:
        host('CheckDisableUpdater', session.install_dir, StateDirectory=session.updater)
    session.data['installation']['disable_updater'] = disable_updater
    session.state('installing')
    try:
        stopped = host('Stop', session.install_dir, StateDirectory=session.processes, ForceStop=args.force_stop)
        session.data['installation']['streamer_was_running'] = bool(stopped.get('WasRunning'))
        session.event('streamer_stopped', stopped)
        # Graceful exit can save settings. Stage the actual change only after
        # exit, preserving the earlier preview and the latest pre-install data.
        session.data['settings_state'] = str(session.directory / 'streamer-install-settings')
        session.data['settings_install_stage'] = host('StageSettings', session.install_dir,
            StateDirectory=session.settings, Account=session.data['account'])
        session.event('post_stop_settings_staged')
        if disable_updater:
            session.event('updater_disable_started')
            disabled = host('DisableUpdater', session.install_dir, StateDirectory=session.updater)
            session.event('updater_disabled', disabled)
        if target:
            target, info = session_device(session)
            if info.installed_sha256 != session.original_apk_hash:
                raise RuntimeError('Headset changed while stopping the Streamer; replacement was not started')
            result = target.replacement_install(session.output / APK_NAME, report['outputs'][APK_NAME],
                session.directory / 'headset-backup', allow_destructive_reset=args.confirm_reinstall,
                allow_data_loss=args.allow_data_loss, stock_apk=session.originals / 'base.apk',
                expected_stock_sha256=session.original_apk_hash)
            if not result.success:
                raise RuntimeError('Headset installation failed: ' + result.message)
            session.data['installation']['headset'] = result.to_dict()
            if result.backup:
                session.data['device_backup'] = result.backup
            session.event('headset_installation_completed')
        session.event('streamer_install_started')
        pc_installer(session, 'Install')
        session.event('streamer_install_completed')
        session.data['installation']['settings_apply_started'] = True
        session.save()
        session.data['installation']['settings'] = host('ApplySettings', session.install_dir,
                                                       StateDirectory=session.settings)
        session.event('pairing_settings_applied')
        if sha(session.install_dir / PC_NAME) != report['outputs'][PC_NAME]:
            raise RuntimeError('Installed Streamer hash does not match the prepared output')
        if target and target.installed_sha256() != report['outputs'][APK_NAME]:
            raise RuntimeError('Installed headset APK hash does not match the prepared output')
        session.state('installed', runtime_tested=False)
        print('Installation verified by file hashes. VR streaming has not been tested.', flush=True)
        print('SESSION_PATH=' + str(session.directory), flush=True)
        if session.data['installation'].get('streamer_was_running'):
            try:
                started = host('Start', session.install_dir, StateDirectory=session.processes)
                session.event('streamer_start_requested', started)
            except Exception as exc:
                session.event('streamer_start_failed', message=str(exc))
                print('Installed successfully; start the Streamer manually: ' + str(exc), flush=True)
    except Exception as exc:
        session.event('installation_failed', message=str(exc))
        recover_after_failure(session, target, args)
        raise
    return session

def restore(args):
    require_admin()
    session = Session.load(args.session)
    if session.data['state'] == 'restored':
        print('This session is already restored.', flush=True)
        return session
    if session.data['state'] not in ('installed', 'installing', 'restoring', 'recovery_required', 'rolled_back'):
        raise RuntimeError('This session has no installed or interrupted operation to restore')
    hashes = session.validate_recovery()
    current = sha(session.install_dir / PC_NAME)
    if current not in (session.original_pc_hash, hashes[PC_NAME]):
        raise RuntimeError('Streamer was updated or changed after installation. Restore stopped to preserve that change.')
    target = None
    if session.data['mode'] == 'adb':
        if not args.confirm_reinstall:
            raise RuntimeError('Restoring the stock APK may remove the local build. Confirm replacement first.')
        target, info = session_device(session)
        if info.installed_sha256 not in (None, session.original_apk_hash, hashes[APK_NAME]):
            raise RuntimeError('Headset app changed after installation. Automatic restore is refused.')
    if session.data['installation'].get('disable_updater'):
        host('CheckRestoreUpdater', session.install_dir, StateDirectory=session.updater)
    session.state('restoring')
    try:
        stopped = host('Stop', session.install_dir, StateDirectory=session.processes, ForceStop=args.force_stop)
        host('CheckRestoreSettings', session.install_dir, StateDirectory=session.settings)
        if target:
            target, info = session_device(session)
            if info.installed_sha256 not in (None, session.original_apk_hash, hashes[APK_NAME]):
                raise RuntimeError('Headset changed while stopping the Streamer; restore was not started')
            # Preserve current settings separately as well as the original
            # pre-patch snapshot. Never silently replace current data without a
            # recoverable copy (unless the operator chose clean reinstall).
            fresh_backup = session.directory / ('before-restore-' + uuid.uuid4().hex[:8])
            try:
                if target.inspect().installed:
                    record = target.backup_private_data(fresh_backup)
                    session.data['before_restore_backup'] = record.to_dict()
                    session.save()
            except device.RootUnavailable:
                if not args.allow_data_loss:
                    raise
            original_backup = session.data.get('original_device_backup', session.data.get('device_backup'))
            result = target.rollback(session.originals / 'base.apk', original_backup,
                allow_destructive_reset=args.confirm_reinstall, allow_data_loss=args.allow_data_loss,
                expected_stock_sha256=session.original_apk_hash)
            if not result.success:
                raise RuntimeError(result.message)
            session.event('stock_headset_restored', result.to_dict())
        if current != session.original_pc_hash:
            pc_installer(session, 'Restore')
        host('RestoreSettings', session.install_dir, StateDirectory=session.settings)
        if session.data['installation'].get('disable_updater'):
            restored_updater = host('RestoreUpdater', session.install_dir, StateDirectory=session.updater)
            session.event('updater_restored', restored_updater)
        session.state('restored')
        if stopped.get('WasRunning'):
            host('Start', session.install_dir, StateDirectory=session.processes)
        print('Original Streamer and applicable headset backup restored.', flush=True)
    except Exception as exc:
        session.state('recovery_required', error=str(exc))
        raise
    return session

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('scan', 'prepare'):
        p = commands.add_parser(name)
        p.add_argument('--apk', type=Path)
        p.add_argument('--streamer', type=Path)
        p.add_argument('--adb', type=Path)
        p.add_argument('--serial')
        p.add_argument('--state-directory', type=Path)
    for name in ('install', 'restore'):
        p = commands.add_parser(name)
        p.add_argument('--session', type=Path, required=True)
        p.add_argument('--confirm-reinstall', action='store_true')
        p.add_argument('--allow-data-loss', action='store_true')
        p.add_argument('--force-stop', action='store_true')
        if name == 'install':
            p.add_argument('--disable-updater', action='store_true',
                           help='Rename Updater.exe to Updater.exe.bak; Restore reverses this change.')
    args = parser.parse_args()
    if args.command == 'scan':
        scan(args)
    else:
        with operation_lock():
            globals()[args.command](args)

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ERROR: ' + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
