Virtual Desktop Streamer local-pairing patcher -- experimental contextual profiles

This is a file patcher with an explicit installer/restore command. It does not
inject a DLL, run the target, change firewall policy, or install automatically.
Supply the original Streamer executable and the SAME pairing.json used for the
headset APK. Python 3.10+ invokes the metadata-only dnlib reader through Windows
PowerShell. Run the parent Build-ManagedTool.ps1 to provision the pinned dnlib
dependency. StreamerInspector.cs is compiled in memory. Pair generation uses
Windows PowerShell 5.1 and Windows CNG; no private key is saved.

Reviewed baseline: 1.34.25.0, SHA-256:
0229a322137f55687240d2b51f5f7002056d9277d7fa001ec6cb4eb25af0bfb8

Compatibility is decided by nine unique method signatures and normalized IL
contexts, not this file hash or its version number. Instructions, resolved
method/field/type identities, string hashes, constants, locals and exception
flow are guarded. Metadata token numbers and physical code locations are
resolved from each input; short/long branches and equivalent argument/local/
integer encodings normalize to the same signature. Changed obfuscated names,
logic, constants or handler flow fail closed and need a reviewed profile.
The actual input SHA-256 binds inspection, patching, installation and restoration.
Other vendor versions are not runtime-tested merely because these guards match.

From the parent patcher directory:

  powershell -NoProfile -ExecutionPolicy Bypass -File streamer\New-Pairing.ps1 -Output .\pairing.json
  python streamer\patch_streamer.py --input "C:\Program Files\Virtual Desktop Streamer\VirtualDesktop.Streamer.exe" --output .\VirtualDesktop.Streamer.exe --pairing .\pairing.json

Use one pairing file for BOTH components. Do not regenerate it between builds.
The generated account is a local pairing name, not a real Meta account. In the
Streamer Accounts tab, add it under Meta Username, and disable Allow remote
connections. Existing account comparison and RSA-PSS verification remain in place.
The patched Streamer trusts only the new key for signed discovery; unmodified
clients carrying official service signatures do not match this key.

Installation (explicit user action, from an elevated PowerShell):
Exit Streamer through its tray menu first. Keep the .exe.patch.json report next
to the patched executable. The installer requires the exact input used to build,
creates a hash-verified original backup, writes recovery information, and replaces
the executable atomically. It does not stop or launch applications.

  powershell -NoProfile -ExecutionPolicy Bypass -File streamer\Install-StreamerPatch.ps1 -Action Install -PatchedExe .\VirtualDesktop.Streamer.exe

Restore:
  powershell -NoProfile -ExecutionPolicy Bypass -File streamer\Install-StreamerPatch.ps1 -Action Restore

The original Authenticode publisher signature is invalidated by modification.
An application update needs a fresh prepare. If its guarded contexts still
match, the tool can build from the new input. Unknown or already patched inputs
fail closed. Restore refuses to overwrite a post-install update. Each original
backup is named with its actual SHA-256; a schema-2 receipt records its filename.

The combined toolchain also offers an optional updater rename. During explicit
install it journals and renames only Updater.exe to Updater.exe.bak. It never
overwrites an existing .bak. Restore requires the matching receipt and original
bytes, and refuses a conflicting newly created Updater.exe. Missing recovery
state never grants ownership of an existing .bak. This option does not prevent
a separate installer or user action from replacing application files.

Observed implementation and review ledger
-----------------------------------------
* A unique, 344-character base64 RSA modulus in the #US heap is replaced by a
  per-pair 2048-bit key. The 65537 exponent and signature-check code stay intact.
* The signature covers byte 0x01 (Quest platform), followed by UTF-8 account name.
  HashAlgorithmName.get_SHA256, RSASignaturePadding.get_Pss and RSA.VerifyData
  are present as MemberRefs 0x0a000397, 0x0a000398, 0x0a000399. A generated CNG
  signature is independently checked with a Python RSA-PSS implementation.
The tokens and IL offsets below identify the reviewed baseline, not lookup keys.

* Method 0x060001ae, ConnectionManager's registration async MoveNext:
  IL_0194 now leaves to IL_058f, after local discovery and TaskCompletionSource
  initialization. This executes the existing finally and builder SetResult.
  IL_0563..IL_056c's two-call self-retry sequence is NOPed to avoid a busy loop.
  Network/account/connection events retain their original reconnect paths.
* Method 0x0600051f returns Task.FromResult((bool?)true), so startup no longer
  waits on the update check before local discovery.
* Method 0x0600018b returns false for the external HTTP HEAD connectivity probe.
* Method 0x0600018c returns immediately instead of starting external traceroute.
* Method 0x0600053f, App.OnStartup, adapts only the self-signature call at
  IL_00b9: pop the path and return true to its existing branch. The modified
  executable cannot retain the publisher's Authenticode signature. The global
  WinVerifyTrust helper, filename guard and all other callers remain unchanged.
* Method 0x0600051d returns nullable false without launching Updater.exe. This
  covers the separate critical-update call made directly by App.OnStartup.
* Method 0x060003fb, UISettings.get_ProductVersion(), calls its original version
  formatter and appends " fkvd" with String.Concat. AssemblyVersion, FileVersion
  and the shared formatter used by numeric/protocol code remain unchanged.
  The suffix reuses the " -critical" user-string entry from the removed updater
  body. Inspection requires that body to be its sole original IL reference.
* Fifteen bounded byte regions change. File size, all metadata tokens and method
  RVAs remain intact. Replacement bodies have shortened declared code sizes
  and their obsolete exception-handler metadata is disabled.

Evidence base: the prior REA Streamer member inventory
ev_f6a5a7c5b19fc2b380cb3559b9833a5ac87ad209ea3a594b93d7d143c7a943f6
and ILSpy/Cecil reconstructions of the same source hash. Method selectors,
normalized IL hashes and instruction-index anchors are in streamer-spec.json.
Development evidence is under evidence/.

Verification and limitations
----------------------------
Host-side Python tests cover actual CNG-generated signatures, invalid signatures/
accounts, changed guarded instructions, changed nonsemantic PE metadata, repeat
patch rejection, exact edit bounds, control-flow anchors and no-overwrite behavior.
The separate host fixture suite includes updater transaction/recovery conflicts.
Mono.Cecil parsed all
modified methods. Installer install/restore/repeat-restore passed on an inert
copied installation; process enumeration was mocked for that fixture. The real
installation remained unchanged.

Adaptive verification: 15 core Python tests and three metadata-profile tests
passed with the suffix. The preceding 26 PowerShell host fixtures cover unchanged
host behavior. A dnlib fixture shifted all nine method
tokens and RVAs; another expanded equivalent IL encodings and changed patch
offsets. Both still matched and patched successfully. A duplicate target method
was rejected as ambiguous. These rewritten assemblies were never executed and
are not deployment artifacts. Installer install/restore/repeat-restore and
changed-current refusal also passed with a relocated fixture's different SHA.
The actual four-instruction footer recipe was also executed in an owned dynamic
method, substituting only a harmless version formatter: "9.8.7" became
"9.8.7 fkvd". No vendor assembly was executed by that test. Numeric metadata
tables are asserted byte-for-byte unchanged. The footer was not visually tested
in a live Streamer window.

Before the footer extension, ILVerify 9.0.0 checked the same 137 methods in
original and patched files against
.NET Framework 4.8 reference assemblies. Original obfuscated code has 476
verifier errors; patched code has 459, with no added diagnostic. This differential
check is NOT a clean verification pass or proof of execution.

These source-only checks do not test headset connection, streaming, reconnect,
service compatibility, Windows publisher-signature behavior, or network capture.
The selected registration/update/probe paths are changed; this does
not prove that every other component or explicit UI action makes no internet
request. Real offline acceptance still requires runtime testing with both paired
outputs and the headset's remaining VR runtime.
