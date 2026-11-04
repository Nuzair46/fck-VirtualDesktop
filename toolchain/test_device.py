"""ADB migration tests. FakeAdb handles every call; no device is accessed."""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
import unittest
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from toolchain import device as d

STOCK = b'unit-test stock APK, not a real APK'
PATCHED = b'unit-test patched APK, not a real APK'
STOCK_HASH = hashlib.sha256(STOCK).hexdigest()
PATCHED_HASH = hashlib.sha256(PATCHED).hexdigest()
REMOTE = '/data/app/~~fake/VirtualDesktop.Android-abc/base.apk'


def make_tar(*, traversal=False, special=False):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:') as archive:
        for root in d.DATA_ROOTS:
            entry = tarfile.TarInfo(root)
            entry.type = tarfile.DIRTYPE
            entry.uid = entry.gid = 10123
            archive.addfile(entry)
        for name, value in [(d.DATA_ROOTS[0] + '/files/settings.json', b'{"quality":"high"}'),
                            (d.DATA_ROOTS[0] + '/cache/stale.bin', b'stale'),
                            (d.DATA_ROOTS[1] + '/shared_prefs/direct.xml', b'<prefs/>'),
                            (d.SHARED_ROOTS[0] + '/files/library.json', b'local library'),
                            (d.SHARED_ROOTS[1] + '/main.obb', b'original OBB')]:
            entry = tarfile.TarInfo(name)
            entry.size = len(value)
            entry.uid = entry.gid = 1023 if name.startswith('data/media/') else 10123
            archive.addfile(entry, io.BytesIO(value))
        link = tarfile.TarInfo(d.DATA_ROOTS[0] + '/lib')
        link.type = tarfile.SYMTYPE
        link.linkname = '/data/app/old-install/lib/arm64'
        archive.addfile(link)
        if traversal:
            entry = tarfile.TarInfo(d.DATA_ROOTS[0] + '/../another.app/secret')
            entry.size = 4
            archive.addfile(entry, io.BytesIO(b'bad!'))
        if special:
            entry = tarfile.TarInfo(d.DATA_ROOTS[0] + '/fifo')
            entry.type = tarfile.FIFOTYPE
            archive.addfile(entry)
    return stream.getvalue()


class FakeAdb:
    def __init__(self):
        self.calls = []
        self.mutations = []
        self.user = 0
        self.hardware = 'QUEST-TEST-HARDWARE'
        self.fallback_hardware = ''
        self.model = 'Quest Pro'
        self.installed = STOCK
        self.uid = 10123
        self.version_name = '1.34.25.0'
        self.version_code = 10722
        self.dump_uid_field = 'userId'
        self.package_uid_override = None
        self.root = 'su'
        self.other_user_app = False
        self.pull_corrupt = False
        self.install_failure = None
        self.install_wrong_hash = False
        self.tar_data = make_tar()
        self.tar_failure = False
        self.restored_tar = None
        self.restore_failure = False
        self.force_stopped = False
        self.devices_text = 'List of devices attached\n192.168.1.2:5555 device product:seacliff model:Quest_Pro\n'

    def __call__(self, args, *, stdout=None, stderr=None, stdin=None, timeout=None, check=None):
        self.calls.append(list(args))
        args = list(args)
        if args[1:] == ['devices', '-l']:
            return self.result(args, self.devices_text)
        assert args[1:3] == ['-s', '192.168.1.2:5555'], args
        command = args[3:]
        if command[:2] == ['shell', '-T']:
            return self.shell(args, command[2], stdin)
        if command[0] == 'exec-out':
            raw = command[1]
            if raw.startswith('su -c '): raw = shlex.split(raw)[2]
            assert raw == 'tar -C / -cf - ' + ' '.join(d.DATA_ROOTS), raw
            stdout.write(self.tar_data)
            self.mutations.append('backup')
            return self.result(args, '', 1 if self.tar_failure else 0, 'tar failed' if self.tar_failure else '')
        if command[0] == 'pull':
            assert command[1] == REMOTE
            Path(command[2]).write_bytes(b'corrupt' if self.pull_corrupt else self.installed)
            return self.result(args, '1 file pulled')
        if command[0] == 'install':
            assert command[1:4] == ['-r', '--user', '0']
            self.mutations.append('install_attempt')
            payload = Path(command[4]).read_bytes()
            if self.install_failure:
                return self.result(args, 'Failure [' + self.install_failure + ']', 1)
            if self.installed is not None and payload != self.installed:
                return self.result(args, 'Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]', 1)
            self.installed = b'wrong installed bytes' if self.install_wrong_hash else payload
            self.uid = 10234
            return self.result(args, 'Success\n')
        if command[0] == 'uninstall':
            assert command == ['uninstall', d.PACKAGE]
            self.mutations.append('uninstall')
            self.installed = None
            return self.result(args, 'Success\n')
        raise AssertionError('Unexpected fake ADB command: ' + repr(command))

    @staticmethod
    def result(args, output='', code=0, error=''):
        return subprocess.CompletedProcess(args, code, output.encode(), error.encode())

    def shell(self, args, command, stdin):
        if command == 'am get-current-user': return self.result(args, str(self.user))
        if command == 'getprop ro.product.model': return self.result(args, self.model)
        if command == 'getprop ro.serialno': return self.result(args, self.hardware)
        if command == 'getprop ro.boot.serialno': return self.result(args, self.fallback_hardware)
        if command == 'dumpsys package ' + d.PACKAGE:
            text = f'  versionName={self.version_name}\n  versionCode={self.version_code} minSdk=29\n  {self.dump_uid_field}={self.uid}\n'
            return self.result(args, text if self.installed is not None else 'Unable to find package')
        if command == 'pm list packages -U --user 0 ' + d.PACKAGE:
            uid = self.uid if self.package_uid_override is None else self.package_uid_override
            return self.result(args, f'package:{d.PACKAGE} uid:{uid}\npackage:{d.PACKAGE}.other uid:19999\n')
        if command == 'pm path --user 0 ' + d.PACKAGE:
            return self.result(args, 'package:' + REMOTE + '\n' if self.installed is not None else '')
        if command.startswith('sha256sum '):
            assert shlex.split(command)[1] == REMOTE
            return self.result(args, hashlib.sha256(self.installed).hexdigest() + '  ' + REMOTE + '\n')
        if command == 'id -u': return self.result(args, '0' if self.root == 'adbd' else '2000')
        if command.startswith('su -c '):
            if self.root != 'su': return self.result(args, '', 1, 'su: permission denied')
            command = shlex.split(command)[2]
            if command == 'id -u': return self.result(args, '0')
        if command.startswith('test -d /data/'):
            assert command[8:] in ['/' + r for r in d.DATA_ROOTS]
            return self.result(args)
        if command == 'am force-stop --user 0 ' + d.PACKAGE:
            self.force_stopped = True
            self.mutations.append('force_stop')
            return self.result(args)
        if command == 'pm clear --user 0 ' + d.PACKAGE:
            self.mutations.append('clear_data')
            return self.result(args, 'Success\n')
        if command.startswith('tar -C / -xf - && chown -R '):
            self.restored_tar = stdin.read()
            self.mutations.append('restore_data')
            return self.result(args, '', 1 if self.restore_failure else 0, 'restore failed' if self.restore_failure else '')
        if command.startswith('stat -c %u:%g '):
            count = len(shlex.split(command)[3:])
            return self.result(args, (f'{self.uid}:{self.uid}\n') * count)
        if command == 'pm list users':
            return self.result(args, 'Users:\n UserInfo{0:Owner:13} running\n UserInfo{10:Guest:10}\n')
        if command == 'pm list packages --user 10 ' + d.PACKAGE:
            return self.result(args, 'package:' + d.PACKAGE if self.other_user_app else '')
        raise AssertionError('Unexpected fake shell command: ' + command)


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='vd_device_tests_')
        self.root = Path(self.tmp.name)
        self.stock = self.root / 'stock.apk'
        self.stock.write_bytes(STOCK)
        self.patched = self.root / 'patched.apk'
        self.patched.write_bytes(PATCHED)
        self.fake = FakeAdb()
        self.events = []
        self.device = d.AdbDevice('fake-adb', '192.168.1.2:5555', runner=self.fake,
                                  on_event=lambda stage, fields: self.events.append((stage, fields)))
        self.hash_patch = mock.patch.object(d, 'STOCK_SHA256', STOCK_HASH)
        self.hash_patch.start()

    def tearDown(self):
        self.hash_patch.stop()
        self.tmp.cleanup()

    def replace(self, **kwargs):
        return self.device.replacement_install(self.patched, PATCHED_HASH, self.root / 'backup',
                                               stock_apk=self.stock, expected_stock_sha256=STOCK_HASH, **kwargs)

    def test_adb_absence_is_allowed(self):
        with mock.patch.object(d.shutil, 'which', return_value=None), mock.patch.object(Path, 'is_file', return_value=False):
            self.assertIsNone(d.discover_adb())

    def _bundled_adb_fixture(self):
        directory = self.root / 'bundled-platform-tools'
        directory.mkdir()
        records = []
        for name in ('adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll'):
            path = directory / name
            path.write_bytes(('fake official package fixture ' + name).encode())
            records.append({'path': name, 'sha256': d.file_sha256(path)})
        (directory / 'manifest.json').write_text(json.dumps({'entrypoint': 'adb.exe', 'files': records}))
        return directory / 'adb.exe'

    def test_verified_bundled_adb_has_priority_over_path(self):
        bundled = self._bundled_adb_fixture()
        with mock.patch.object(d, 'BUNDLED_ADB', bundled), mock.patch.object(d.shutil, 'which', return_value='another-adb.exe'):
            self.assertEqual(d.discover_adb(), bundled.resolve())
        self.assertEqual(self.fake.calls, [])

    def test_explicit_external_adb_overrides_bundled(self):
        bundled = self._bundled_adb_fixture()
        external = self.root / 'external-adb.exe'
        external.write_bytes(b'operator-selected external tool')
        with mock.patch.object(d, 'BUNDLED_ADB', bundled):
            self.assertEqual(d.discover_adb(external), external.resolve())

    def test_modified_bundled_adb_dependency_is_rejected(self):
        bundled = self._bundled_adb_fixture()
        (bundled.parent / 'AdbWinApi.dll').write_bytes(b'changed dependency')
        with mock.patch.object(d, 'BUNDLED_ADB', bundled):
            with self.assertRaises(d.DeviceError): d.discover_adb()

    def test_multiple_devices_require_explicit_selection(self):
        self.fake.devices_text += 'USB123 device model:Quest_Pro\n'
        with self.assertRaises(d.DeviceSelectionError): d.select_device('fake-adb', runner=self.fake)
        selected = d.select_device('fake-adb', '192.168.1.2:5555', runner=self.fake)
        self.assertEqual(selected.serial, self.device.serial)

    def test_unauthorized_device_is_not_selected(self):
        self.fake.devices_text = 'List of devices attached\n192.168.1.2:5555 unauthorized\n'
        with self.assertRaises(d.DeviceSelectionError): d.select_device('fake-adb', runner=self.fake)
        self.assertEqual(self.fake.mutations, [])

    def test_inspect_hardware_serial_and_fallback(self):
        info = self.device.inspect().require_supported()
        self.assertEqual(info.hardware_serial, self.fake.hardware)
        self.assertEqual(info.installed_sha256, STOCK_HASH)
        self.fake.hardware = ''
        self.fake.fallback_hardware = 'BOOT-SERIAL'
        self.assertEqual(self.device.inspect().hardware_serial, 'BOOT-SERIAL')
        json.dumps(info.to_dict())

    def test_nonowner_user_refused_before_any_install(self):
        self.fake.user = 10
        with self.assertRaises(d.UnsupportedDeviceError): self.device.install(self.patched, PATCHED_HASH)
        self.assertEqual(self.fake.mutations, [])

    def test_modern_quest_appid_is_corroborated_by_exact_package_uid(self):
        self.fake.dump_uid_field = 'appId'
        self.fake.uid = 10000
        info = self.device.inspect()
        self.assertEqual(info.uid, 10000)
        self.assertTrue(any('pm list packages -U --user 0 ' + d.PACKAGE in call for call in self.fake.calls))
        self.assertEqual(self.fake.mutations, [])

    def test_uid_disagreement_is_rejected_before_mutations(self):
        self.fake.package_uid_override = 19999
        with self.assertRaises(d.DeviceError): self.device.inspect()
        self.assertEqual(self.fake.mutations, [])

    def test_pull_verifies_local_and_remote_hash(self):
        destination = self.root / 'copy.apk'
        copied = self.device.pull_original(destination, expected_sha256=STOCK_HASH)
        self.assertEqual(destination.read_bytes(), STOCK)
        self.assertEqual(copied.sha256, STOCK_HASH)
        self.assertEqual(self.fake.mutations, [])

    def test_pull_wrong_original_refused(self):
        with self.assertRaises(d.UnsupportedDeviceError):
            self.device.pull_original(self.root / 'copy.apk', expected_sha256='0' * 64)
        self.assertFalse(any('pull' in call for call in self.fake.calls))

    def test_future_version_pull_uses_selected_actual_hash(self):
        self.fake.version_name = '1.35.9.0'
        self.fake.version_code = 12099
        self.fake.installed = b'new source APK with a different whole-file hash'
        record = self.device.pull_original(self.root / 'new.apk')
        self.assertEqual(record.version_name, '1.35.9.0')
        self.assertEqual(record.version_code, 12099)
        self.assertEqual(record.sha256, hashlib.sha256(self.fake.installed).hexdigest())

    def test_replacement_requires_prepared_recovery_hash(self):
        with self.assertRaises(d.DeviceError):
            self.device.replacement_install(self.patched, PATCHED_HASH, self.root / 'backup',
                stock_apk=self.stock, allow_destructive_reset=True)
        self.assertNotIn('uninstall', self.fake.mutations)

    def test_rollback_requires_session_hash_without_backup(self):
        with self.assertRaises(d.DeviceError):
            self.device.rollback(self.stock, allow_destructive_reset=True, allow_data_loss=True)
        self.assertNotIn('install_attempt', self.fake.mutations)

    def test_rollback_accepts_new_source_hash_from_session(self):
        self.fake.installed = None
        self.fake.version_name = '1.35.9.0'
        self.fake.version_code = 12099
        self.stock.write_bytes(b'new original source APK')
        actual = d.file_sha256(self.stock)
        result = self.device.rollback(self.stock, expected_stock_sha256=actual)
        self.assertTrue(result.success)
        self.assertEqual(result.device['installed_sha256'], actual)

    def test_pull_corruption_retains_only_partial(self):
        self.fake.pull_corrupt = True
        destination = self.root / 'copy.apk'
        with self.assertRaises(d.DeviceError): self.device.pull_original(destination, expected_sha256=STOCK_HASH)
        self.assertFalse(destination.exists())
        self.assertTrue(destination.with_suffix('.apk.partial').exists())

    def test_signature_mismatch_does_not_uninstall_without_consent(self):
        result = self.replace()
        self.assertTrue(result.signature_mismatch)
        self.assertNotIn('uninstall', self.fake.mutations)
        self.assertNotIn('backup', self.fake.mutations)

    def test_unrelated_install_failure_never_becomes_uninstall(self):
        self.fake.install_failure = 'INSTALL_FAILED_INSUFFICIENT_STORAGE'
        result = self.replace(allow_destructive_reset=True, allow_data_loss=True)
        self.assertEqual(result.status, 'install_failed')
        self.assertNotIn('uninstall', self.fake.mutations)

    def test_missing_recovery_apk_blocks_replacement(self):
        with self.assertRaises(d.DeviceError):
            self.device.replacement_install(self.patched, PATCHED_HASH, self.root / 'backup', allow_destructive_reset=True)
        self.assertNotIn('uninstall', self.fake.mutations)

    def test_rooted_backup_is_verified_and_json_round_trips(self):
        record = self.device.backup_private_data(self.root / 'backup')
        self.assertEqual(record.sha256, hashlib.sha256(self.fake.tar_data).hexdigest())
        self.assertTrue(self.fake.force_stopped)
        self.assertEqual(d.BackupRecord.from_dict(json.loads(json.dumps(record.to_dict()))), record)

    def test_adbd_root_does_not_require_su(self):
        self.fake.root = 'adbd'
        record = self.device.backup_private_data(self.root / 'backup')
        self.assertEqual(record.root_mode, 'adbd')
        self.assertFalse(any('su -c' in arg for call in self.fake.calls for arg in call))

    def test_no_root_default_refuses_data_loss(self):
        self.fake.root = None
        with self.assertRaises(d.RootUnavailable): self.replace(allow_destructive_reset=True)
        self.assertNotIn('uninstall', self.fake.mutations)

    def test_no_root_explicit_data_loss_allows_clean_reinstall(self):
        self.fake.root = None
        result = self.replace(allow_destructive_reset=True, allow_data_loss=True)
        self.assertTrue(result.success and result.data_reset)
        self.assertFalse(result.data_restored)
        self.assertEqual(self.fake.installed, PATCHED)

    def test_rooted_replacement_preserves_data_with_new_uid(self):
        result = self.replace(allow_destructive_reset=True)
        self.assertTrue(result.success and result.data_restored)
        self.assertLess(self.fake.mutations.index('backup'), self.fake.mutations.index('uninstall'))
        with tarfile.open(fileobj=io.BytesIO(self.fake.restored_tar), mode='r:') as archive:
            entries = archive.getmembers()
            self.assertTrue(all(e.uid == 10234 and e.gid == 10234 for e in entries if not e.name.startswith('data/media/')))
            self.assertFalse(any(e.issym() or '/cache/' in e.name for e in entries))
            self.assertEqual(archive.extractfile(d.DATA_ROOTS[0] + '/files/settings.json').read(), b'{"quality":"high"}')
            shared = archive.getmember(d.SHARED_ROOTS[0] + '/files/library.json')
            self.assertEqual((shared.uid, shared.gid), (1023, 1023))
            self.assertEqual(archive.extractfile(d.SHARED_ROOTS[1] + '/main.obb').read(), b'original OBB')
        self.assertTrue(any(stage == 'private_restore_completed' for stage, _ in self.events))

    def test_other_android_user_install_blocks_global_uninstall(self):
        self.fake.other_user_app = True
        with self.assertRaises(d.UnsupportedDeviceError): self.replace(allow_destructive_reset=True)
        self.assertNotIn('uninstall', self.fake.mutations)

    def test_reused_adb_address_cannot_restore_other_hardware(self):
        backup = self.device.backup_private_data(self.root / 'backup')
        self.fake.hardware = 'DIFFERENT-HEADSET'
        with self.assertRaises(d.DataBackupError): self.device.restore_private_data(backup)
        self.assertNotIn('clear_data', self.fake.mutations)

    def test_corrupted_backup_blocks_uninstall(self):
        backup = self.device.backup_private_data(self.root / 'backup')
        Path(backup.archive).write_bytes(b'bad')
        with self.assertRaises(d.DataBackupError): self.replace(allow_destructive_reset=True, backup_record=backup)
        self.assertNotIn('uninstall', self.fake.mutations)

    def test_tar_command_failure_never_ignored_by_data_loss_optin(self):
        self.fake.tar_failure = True
        with self.assertRaises(d.DataBackupError): self.replace(allow_destructive_reset=True, allow_data_loss=True)
        self.assertNotIn('uninstall', self.fake.mutations)

    def test_tar_traversal_rejected(self):
        path = self.root / 'bad.tar'
        path.write_bytes(make_tar(traversal=True))
        with self.assertRaises(d.DataBackupError): d.validate_backup_tar(path)

    def test_tar_special_file_rejected(self):
        path = self.root / 'bad.tar'
        path.write_bytes(make_tar(special=True))
        with self.assertRaises(d.DataBackupError): d.validate_backup_tar(path)

    def test_tar_truncation_rejected(self):
        path = self.root / 'bad.tar'
        path.write_bytes(self.fake.tar_data[:900])
        with self.assertRaises(d.DataBackupError): d.validate_backup_tar(path)

    def test_successful_install_still_requires_remote_hash(self):
        self.fake.installed = None
        self.fake.install_wrong_hash = True
        with self.assertRaises(d.InstallVerificationError): self.device.install(self.patched, PATCHED_HASH)

    def test_rollback_requires_its_own_destructive_optin(self):
        backup = self.device.backup_private_data(self.root / 'backup')
        self.fake.installed = PATCHED
        result = self.device.rollback(self.stock, backup)
        self.assertTrue(result.signature_mismatch)
        self.assertNotIn('uninstall', self.fake.mutations)

    def test_explicit_rollback_restores_stock_and_data(self):
        backup = self.device.backup_private_data(self.root / 'backup')
        self.fake.installed = PATCHED
        result = self.device.rollback(self.stock, backup, allow_destructive_reset=True)
        self.assertTrue(result.success and result.data_restored)
        self.assertEqual(self.fake.installed, STOCK)
        self.assertEqual(result.status, 'rolled_back')

    def test_changed_output_during_backup_blocks_uninstall(self):
        def change_output(stage, fields):
            if stage == 'private_backup_verified': self.patched.write_bytes(b'changed after build')
        self.device.on_event = change_output
        with self.assertRaises(d.DeviceError): self.replace(allow_destructive_reset=True)
        self.assertNotIn('uninstall', self.fake.mutations)

    def test_restore_retry_keeps_failed_stage_and_uses_unique_new_stage(self):
        backup = self.device.backup_private_data(self.root / 'backup')
        self.fake.restore_failure = True
        with self.assertRaises(d.DeviceError): self.device.restore_private_data(backup)
        failed = list((self.root / 'backup').glob('private-data.restore-*.tar'))
        self.assertEqual(len(failed), 1)
        failed_hash = d.file_sha256(failed[0])
        self.fake.restore_failure = False
        result = self.device.restore_private_data(backup)
        self.assertTrue(result['restored'])
        self.assertEqual(list((self.root / 'backup').glob('private-data.restore-*.tar')), failed)
        self.assertEqual(d.file_sha256(failed[0]), failed_hash)
        stages = [fields['restore_staging_path'] for stage, fields in self.events if stage == 'private_restore_started']
        self.assertEqual(len(stages), 2)
        self.assertNotEqual(*stages)

    def test_bound_device_refuses_install_after_hardware_rebound(self):
        self.device.bind_identity(self.device.inspect())
        self.fake.hardware = 'REUSED-IP-DIFFERENT-HEADSET'
        with self.assertRaises(d.UnsupportedDeviceError): self.device.install(self.patched, PATCHED_HASH)
        self.assertEqual(self.fake.mutations, [])

    def test_hardware_drift_during_backup_blocks_uninstall(self):
        def change_hardware(stage, fields):
            if stage == 'private_backup_verified': self.fake.hardware = 'NEW-DEVICE'
        self.device.on_event = change_hardware
        with self.assertRaises(d.UnsupportedDeviceError): self.replace(allow_destructive_reset=True)
        self.assertNotIn('uninstall', self.fake.mutations)

    def test_installed_apk_drift_during_backup_blocks_uninstall(self):
        def change_package(stage, fields):
            if stage == 'private_backup_verified': self.fake.installed = b'changed installed APK'
        self.device.on_event = change_package
        with self.assertRaises(d.UnsupportedDeviceError): self.replace(allow_destructive_reset=True)
        self.assertNotIn('uninstall', self.fake.mutations)


if __name__ == '__main__': unittest.main(verbosity=2)
