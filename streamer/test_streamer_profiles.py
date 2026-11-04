"""Static metadata-layout fixtures; never execute rewritten vendor assemblies."""
import json
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

import patch_streamer as patch

ROOT=Path(__file__).parent
SOURCE=Path(r'C:\Program Files\Virtual Desktop Streamer\VirtualDesktop.Streamer.exe')

class StreamerProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='vd-streamer-profile-tests-')
        cls.fixtures=Path(os.environ.get('VD_STREAMER_FIXTURE_DIR',cls.temp.name))
        cls.baseline=patch.inspect_streamer(SOURCE)
        cls.pair=json.loads((ROOT/'test-pairing.json').read_text())
        for mode,filename in [('layout','relocated-test.exe'),('encoding','encoding-test.exe'),('ambiguous','ambiguous-test.exe')]:
            destination=cls.fixtures/filename
            if not destination.exists():
                subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                    str(ROOT/'New-StreamerFixture.ps1'),'-InputPath',str(SOURCE),
                    '-OutputPath',str(destination),'-Mode',mode],check=True,capture_output=True,timeout=180)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check_compatible(self,name):
        source=self.fixtures/name
        inspected=patch.inspect_streamer(source)
        self.assertNotEqual(inspected['source_sha256'],self.baseline['source_sha256'])
        data=source.read_bytes()
        output,report=patch.build_patch(data,self.pair['modulus_base64'],_inspection=inspected)
        self.assertEqual(len(report['patches']),12)
        self.assertEqual(report['source_sha256'],patch.sha256(data))
        self.assertEqual(len(output),len(data))
        methods={m['id']:m for m in inspected['methods']}
        reg=methods['registration'];task=methods['update_task']
        code=patch._rva_offset(reg['rva'],patch._pe_sections(data))+12
        site=reg['sites']['offline_leave']['offset'];target=reg['sites']['completion']['offset']
        self.assertEqual(output[code+site],0xdd)
        self.assertEqual(site+5+struct.unpack_from('<i',output,code+site+1)[0],target)
        task_code=patch._rva_offset(task['rva'],patch._pe_sections(data))+12
        self.assertEqual(struct.unpack_from('<I',output,task_code+2)[0],reg['sites']['nullable_ctor']['token'])
        self.assertEqual(struct.unpack_from('<I',output,task_code+7)[0],reg['sites']['from_result']['token'])
        return inspected

    def test_shifted_metadata_tokens_and_rvas(self):
        inspected=self.check_compatible('relocated-test.exe')
        for old,new in zip(self.baseline['methods'],inspected['methods']):
            self.assertNotEqual(old['token'],new['token'])
            self.assertNotEqual(old['rva'],new['rva'])
            self.assertEqual(old['semantic_sha256'],new['semantic_sha256'])

    def test_equivalent_il_encodings_with_new_offsets(self):
        inspected=self.check_compatible('encoding-test.exe')
        old=self.baseline['methods'][0]['sites']['offline_leave']['offset']
        new=inspected['methods'][0]['sites']['offline_leave']['offset']
        self.assertNotEqual(old,new)

    def test_duplicate_target_method_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'missing or ambiguous: update_task'):
            patch.inspect_streamer(self.fixtures/'ambiguous-test.exe')

if __name__=='__main__':unittest.main()
