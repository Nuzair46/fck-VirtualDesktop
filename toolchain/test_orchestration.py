"""Transaction tests against file fixtures and fake hosts; no device/app mutation."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import toolchain_cli as app

PC = b'fixture original PC'
APK = b'fixture original APK'
NEW_PC = b'fixture patched PC'
NEW_APK = b'fixture patched APK'
H = lambda b: hashlib.sha256(b).hexdigest()

class FakeDevice:
    def __init__(self, session, events):
        self.session, self.events = session, events
        self.current = H(APK)
        self.fail = False
    def inspect(self):
        return SimpleNamespace(installed=True, installed_sha256=self.current)
    def installed_sha256(self): return self.current
    def replacement_install(self, *args, **kwargs):
        self.events.append('DeviceReplace')
        self.session.event('private_backup_verified', {'archive':'fixture-original','source_apk_sha256':H(APK)})
        self.session.event('apk_uninstall_started')
        self.current = H(NEW_APK)
        return SimpleNamespace(success=not self.fail, message='fixture install failure' if self.fail else '',
            backup=self.session.data['device_backup'], to_dict=lambda:{'success':not self.fail})
    def rollback(self, *args, **kwargs):
        self.events.append('DeviceRollback')
        self.current = H(APK)
        return SimpleNamespace(success=True, message='', to_dict=lambda:{'success':True})
    def backup_private_data(self, directory):
        self.events.append('BackupBeforeRestore')
        value={'archive':'fixture-later', 'source_apk_sha256':self.current}
        self.session.event('private_backup_verified',value)
        return SimpleNamespace(to_dict=lambda:value)

class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.events=[]
        self.directory=self.root/'installed';self.directory.mkdir()
        (self.directory/app.PC_NAME).write_bytes(PC)
        self.session=app.Session.create(self.root/'state','adb')
        self.session.originals.mkdir()
        (self.session.originals/app.PC_NAME).write_bytes(PC)
        (self.session.originals/'base.apk').write_bytes(APK)
        self.session.output.mkdir()
        (self.session.output/app.PC_NAME).write_bytes(NEW_PC)
        (self.session.output/app.APK_NAME).write_bytes(NEW_APK)
        hashes={app.PC_NAME:H(NEW_PC),app.APK_NAME:H(NEW_APK)}
        report={'version':'1.99.1.0','inputs':{'apk':{'sha256':H(APK)},'streamer':{'sha256':H(PC)}},
                'compatibility':{'matched':True},'outputs':hashes}
        app.atomic_json(self.session.output/'build-report.json',report)
        self.session.data.update(state='prepared',account='vdlocalfixture',output_hashes=hashes,
            input_hashes={app.PC_NAME:H(PC),'base.apk':H(APK)},
            build_report_sha256=app.sha(self.session.output/'build-report.json'),
            streamer=self.info(),device={'serial':'fixture','hardware_serial':'hardware','model':'Quest Pro','android_user':0})
        self.session.save()
        self.dev=FakeDevice(self.session,self.events)
        self.patches=[patch.object(app,'require_admin'),patch.object(app,'host',side_effect=self.host),
            patch.object(app,'pc_installer',side_effect=self.installer),
            patch.object(app,'session_device',side_effect=self.get_device)]
        for p in self.patches:p.start()
        self.fail_host=None; self.fail_installer=False
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def info(self):
        return {'Found':True,'IsAmbiguous':False,'Version':'1.99.1.0','Sha256':app.sha(self.directory/app.PC_NAME),
                'Executable':str(self.directory/app.PC_NAME),'InstallDirectory':str(self.directory)}
    def args(self, **kwargs):
        result=dict(session=self.session.directory,confirm_reinstall=True,allow_data_loss=False,force_stop=False,disable_updater=False)
        result.update(kwargs);return argparse.Namespace(**result)
    def host(self, action, directory=None, **kwargs):
        self.events.append(action)
        if action==self.fail_host:raise RuntimeError('fixture '+action+' failure')
        if action=='Detect':return self.info()
        if action in ('Stop','Start'):
            self.assertTrue(kwargs.get('StateDirectory'))
            return {'WasRunning':False}
        return {'Action':action}
    def installer(self, session, action):
        self.events.append('PC'+action)
        if self.fail_installer and action=='Install':raise RuntimeError('fixture PC failure')
        (self.directory/app.PC_NAME).write_bytes(NEW_PC if action=='Install' else PC)
        return {'Action':action}
    def get_device(self, session):
        self.dev.session=session
        return self.dev,self.dev.inspect()
    def latest(self):return app.Session.load(self.session.directory)
    def test_manual_install_does_not_access_adb(self):
        self.session.data['mode']='manual';self.session.save()
        with patch.object(app,'session_device',side_effect=AssertionError('ADB touched')):
            result=app.install(self.args(confirm_reinstall=False))
        self.assertEqual(result.data['state'],'installed')
        self.assertNotIn('DeviceReplace',self.events)

    def test_manual_detection_does_not_access_adb(self):
        args=argparse.Namespace(streamer=None,apk=None,pc_only=True,adb=None,serial=None)
        with patch.object(app.device,'discover_adb',side_effect=AssertionError('ADB touched')):
            result=app.scan(args)
        self.assertEqual(result['mode'],'manual')
        self.assertNotIn('adb_path',result)
    def test_no_consent_means_no_stop_or_install(self):
        with self.assertRaises(RuntimeError):app.install(self.args(confirm_reinstall=False))
        self.assertNotIn('Stop',self.events);self.assertNotIn('DeviceReplace',self.events)
    def test_both_outputs_ready_before_any_mutation(self):
        (self.session.output/app.APK_NAME).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):app.install(self.args())
        self.assertNotIn('Stop',self.events)
    def test_sequence_and_original_snapshot_retained(self):
        result=app.install(self.args())
        self.assertEqual(result.data['state'],'installed')
        for first,second in [('Stop','StageSettings'),('StageSettings','DeviceReplace'),('DeviceReplace','PCInstall'),('PCInstall','ApplySettings')]:
            self.assertLess(self.events.index(first),self.events.index(second))
        self.assertEqual(result.data['original_device_backup']['archive'],'fixture-original')
    def test_pc_failure_recovers_headset(self):
        self.fail_installer=True
        with self.assertRaises(RuntimeError):app.install(self.args())
        self.assertEqual(self.latest().data['state'],'rolled_back')
        self.assertEqual(self.dev.current,H(APK))
        self.assertIn('DeviceRollback',self.events)
    def test_settings_failure_recovers_both(self):
        self.fail_host='ApplySettings'
        with self.assertRaises(RuntimeError):app.install(self.args())
        self.assertEqual(self.latest().data['state'],'rolled_back')
        self.assertIn('PCRestore',self.events)
        self.assertIn('RestoreSettings',self.events)
        self.assertEqual(self.dev.current,H(APK))
    def test_restore_preflights_settings_before_device_change(self):
        app.install(self.args());self.events.clear();self.fail_host='CheckRestoreSettings'
        with self.assertRaises(RuntimeError):app.restore(self.args())
        self.assertNotIn('DeviceRollback',self.events)
        self.assertNotIn('PCRestore',self.events)
    def test_recovery_does_not_require_generated_outputs(self):
        app.install(self.args())
        (self.session.output/app.APK_NAME).unlink()
        (self.session.output/'build-report.json').unlink()
        result=app.restore(self.args())
        self.assertEqual(result.data['state'],'restored')
    def test_restore_backup_does_not_replace_original_snapshot(self):
        app.install(self.args())
        result=app.restore(self.args())
        self.assertEqual(result.data['original_device_backup']['archive'],'fixture-original')
        self.assertEqual(result.data['before_restore_backup']['archive'],'fixture-later')
    def test_restore_refuses_unknown_pc_update(self):
        app.install(self.args());self.events.clear()
        (self.directory/app.PC_NAME).write_bytes(b'new unknown version')
        with self.assertRaises(RuntimeError):app.restore(self.args())
        self.assertNotIn('Stop',self.events)
    def test_original_corruption_blocks_recovery(self):
        (self.session.originals/'base.apk').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):self.session.validate_recovery()
    def test_restore_repeats_without_mutations(self):
        app.install(self.args());app.restore(self.args());self.events.clear()
        app.restore(self.args());self.assertEqual(self.events,[])

    def test_updater_left_unchanged_by_default(self):
        app.install(self.args());app.restore(self.args())
        self.assertFalse(any('Updater' in action for action in self.events))

    def test_updater_option_preflight_disable_restore_order(self):
        app.install(self.args(disable_updater=True))
        for first,second in [('CheckDisableUpdater','Stop'),('Stop','DisableUpdater'),('DisableUpdater','DeviceReplace')]:
            self.assertLess(self.events.index(first),self.events.index(second))
        self.events.clear()
        app.restore(self.args())
        self.assertLess(self.events.index('CheckRestoreUpdater'),self.events.index('DeviceRollback'))
        self.assertIn('RestoreUpdater',self.events)

    def test_updater_collision_stops_before_app_mutations(self):
        self.fail_host='CheckDisableUpdater'
        with self.assertRaises(RuntimeError):app.install(self.args(disable_updater=True))
        self.assertNotIn('Stop',self.events)
        self.assertNotIn('DeviceReplace',self.events)
        self.assertEqual(self.latest().data['state'],'prepared')

    def test_install_failure_restores_updater(self):
        self.fail_installer=True
        with self.assertRaises(RuntimeError):app.install(self.args(disable_updater=True))
        self.assertIn('RestoreUpdater',self.events)
        self.assertEqual(self.latest().data['state'],'rolled_back')

    def test_updater_restore_collision_stops_before_app_mutations(self):
        app.install(self.args(disable_updater=True));self.events.clear()
        self.fail_host='CheckRestoreUpdater'
        with self.assertRaises(RuntimeError):app.restore(self.args())
        self.assertNotIn('Stop',self.events)
        self.assertNotIn('DeviceRollback',self.events)

    def test_streamer_changed_since_prepare_stops_before_mutation(self):
        (self.directory/app.PC_NAME).write_bytes(b'new version after prepare')
        with self.assertRaises(RuntimeError):app.install(self.args())
        self.assertNotIn('Stop',self.events)

    def test_report_change_stops_before_mutation(self):
        report=self.session.output/'build-report.json'
        value=json.loads(report.read_text());value['compatibility']['matched']=False
        app.atomic_json(report,value)
        with self.assertRaises(ValueError):app.install(self.args())
        self.assertNotIn('Stop',self.events)

if __name__=='__main__':unittest.main()
