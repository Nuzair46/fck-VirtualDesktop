"""Host-only tests. Never executes VD or modifies its installation."""
import copy
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

import patch_streamer as patch

ROOT=Path(__file__).parent
SOURCE=Path(os.environ.get('VD_STREAMER_ORIGINAL',r'C:\Program Files\Virtual Desktop Streamer\VirtualDesktop.Streamer.exe'))

class StreamerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pair=json.loads((ROOT/'test-pairing.json').read_text())
        cls.original=SOURCE.read_bytes()
        cls.patched,cls.report=patch.build_patch(cls.original,cls.pair['modulus_base64'])

    def test_real_cng_signature(self):
        patch.validate_pairing(self.pair)

    def test_changed_account_rejected(self):
        pair=copy.deepcopy(self.pair)
        pair['account']='otherlocal'
        with self.assertRaises(ValueError): patch.validate_pairing(pair)

    def test_corrupt_signature_rejected(self):
        pair=copy.deepcopy(self.pair)
        raw=bytearray(patch.decode64(pair['signature_base64']))
        raw[37]^=1
        pair['signature_base64']=patch.base64.b64encode(raw).decode()
        with self.assertRaises(ValueError): patch.validate_pairing(pair)

    def test_changed_guarded_instruction_rejected(self):
        altered=bytearray(self.original)
        altered[204984+12]=0x00  # critical updater's first instruction
        with self.assertRaises(ValueError): patch.build_patch(bytes(altered),self.pair['modulus_base64'])

    def test_different_global_hash_with_matching_context_is_supported(self):
        altered=bytearray(self.original)
        pe=struct.unpack_from('<I',altered,60)[0]
        altered[pe+8]^=1  # COFF timestamp, outside all IL contexts
        changed,report=patch.build_patch(bytes(altered),self.pair['modulus_base64'])
        self.assertNotEqual(report['source_sha256'],patch.ORIGINAL_SHA256)
        self.assertEqual(report['source_sha256'],patch.sha256(altered))
        self.assertEqual(len(changed),len(altered))
        self.assertTrue(report['compatibility']['compatible'])

    def test_guarded_signature_ambiguity_is_rejected(self):
        profile=json.loads((ROOT/'streamer-spec.json').read_text())
        profile['methods'][1]['signature']=profile['methods'][0]['signature']
        # A profile cannot smuggle a second role under another method fingerprint.
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'profile.json';file.write_text(json.dumps(profile))
            process=patch.subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass',
                '-File',str(ROOT/'Inspect-Streamer.ps1'),'-InputPath',str(SOURCE),'-ProfilePath',str(file)],capture_output=True,encoding='utf-8')
            self.assertNotEqual(process.returncode,0)
            self.assertIn('signature mismatch',process.stdout)

    def test_patch_again_rejected(self):
        with self.assertRaises(ValueError): patch.build_patch(self.patched,self.pair['modulus_base64'])

    def test_only_fifteen_bounded_regions(self):
        self.assertEqual(len(self.patched),len(self.original))
        self.assertEqual(len(self.report['patches']),15)
        allowed=set()
        for edit in self.report['patches']:
            allowed.update(range(edit['offset'],edit['offset']+edit['size']))
        changed={i for i,(a,b) in enumerate(zip(self.original,self.patched)) if a!=b}
        self.assertTrue(changed)
        self.assertTrue(changed<=allowed)
        self.assertEqual(self.report['changed_byte_count'],len(changed))

    def test_lan_exit_and_retry_removal(self):
        code=59432+12
        self.assertEqual(self.patched[code+404],0xdd)
        self.assertEqual(409+struct.unpack_from('<i',self.patched,code+405)[0],1423)
        self.assertEqual(self.patched[code+1379:code+1389],bytes(10))
        # LAN-listener call itself remains unchanged.
        self.assertEqual(self.patched[code+359:code+404],self.original[code+359:code+404])

    def test_original_auth_logic_unchanged(self):
        # Every edit is one of seven reviewed methods or the two #US entries.
        # In particular no signature-check branch or account comparison changes.
        ranges=[(59432,59432+12+1612),(205464,205464+12+60),
                (56124,56124+12+123),(56288,56288+12+68),(211400+12+185,211400+12+190),
                (204984,204984+12+304)]
        footer=next(m for m in self.report['compatibility']['methods'] if m['id']=='footer_version')
        footer_pos=patch._rva_offset(footer['rva'],patch._pe_sections(self.original))
        ranges.append((footer_pos,footer_pos+12+footer['code_size']))
        suffix=next(row for row in self.report['patches'] if row['reason'].startswith('Reuse exclusively'))
        ranges.append((suffix['offset'],suffix['offset']+suffix['size']))
        modulus_pos=self.original.index(patch.ORIGINAL_MODULUS.encode('utf-16-le'))
        ranges.append((modulus_pos,modulus_pos+688))
        for row in self.report['patches']:
            self.assertTrue(any(lo<=row['offset'] and row['offset']+row['size']<=hi for lo,hi in ranges))

    def test_only_startup_self_signature_call_adapted(self):
        start=211400+12
        self.assertEqual(self.patched[start+185:start+190],b'\x26\x17\0\0\0')
        self.assertEqual(self.original[start+185],0x28)
        # The existing success/failure branch and filename guard are unchanged.
        self.assertEqual(self.patched[start:start+185],self.original[start:start+185])
        self.assertEqual(self.patched[start+190:start+958],self.original[start+190:start+958])

    def test_external_critical_updater_is_not_launched(self):
        header=204984
        self.assertEqual(struct.unpack_from('<I',self.patched,header+4)[0],7)
        self.assertEqual(self.patched[header+12:header+19],b'\x16\x73'+struct.pack('<I',167774220)+b'\x2a')

    def test_footer_appends_suffix_without_changing_numeric_versions(self):
        inspected=self.report['compatibility']
        footer=next(m for m in inspected['methods'] if m['id']=='footer_version')
        pos=patch._rva_offset(footer['rva'],patch._pe_sections(self.original))
        self.assertEqual(struct.unpack_from('<I',self.patched,pos+4)[0],16)
        body=self.patched[pos+12:pos+28]
        self.assertEqual([body[0],body[5],body[10],body[15]],[0x28,0x72,0x28,0x2a])
        self.assertEqual(struct.unpack_from('<I',body,1)[0],footer['sites']['version_formatter']['token'])
        self.assertEqual(struct.unpack_from('<I',body,11)[0],inspected['footer']['concat_token'])
        string_token=struct.unpack_from('<I',body,6)[0]
        entry=patch._metadata_stream_offset(self.patched,'#US')+(string_token&0xffffff)
        length=self.patched[entry]
        suffix=self.patched[entry+1:entry+length].decode('utf-16-le')
        self.assertEqual(suffix,' fkvd')
        self.assertEqual('1.34.25'+suffix,'1.34.25 fkvd')
        self.assertEqual(self.report['version'],'1.34.25.0')
        self.assertEqual(inspected['assembly_version'],'1.34.25.0')
        # Numeric version metadata tables precede #Strings and are untouched.
        start=patch._metadata_stream_offset(self.original,'#~')
        end=patch._metadata_stream_offset(self.original,'#Strings')
        self.assertEqual(self.patched[start:end],self.original[start:end])

    def test_footer_instructions_execute_in_owned_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'VirtualDesktop.Streamer.exe'
            target.write_bytes(self.patched)
            process=patch.subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass',
                '-File',str(ROOT/'Test-StreamerFooter.ps1'),'-PatchedPath',str(target)],
                check=True,capture_output=True,encoding='utf-8')
            result=json.loads(process.stdout)
            self.assertEqual(result['Actual'],'9.8.7 fkvd')
            self.assertFalse(result['VendorAssemblyExecuted'])

    def test_no_overwrite_and_output_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            destination=Path(temp)/'VirtualDesktop.Streamer.exe'
            report=patch.patch_streamer(SOURCE,destination,self.pair['modulus_base64'])
            self.assertEqual(patch.sha256(destination.read_bytes()),report['output_sha256'])
            with self.assertRaises(ValueError): patch.patch_streamer(SOURCE,destination,self.pair['modulus_base64'])
        self.assertEqual(patch.sha256(SOURCE.read_bytes()),patch.ORIGINAL_SHA256)

if __name__=='__main__': unittest.main()
