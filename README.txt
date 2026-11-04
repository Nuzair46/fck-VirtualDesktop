Virtual Desktop Offline Tool
===========================

The release download is VirtualDesktop-OfflineTool.exe. Run it on Windows x64.
It unpacks its bundled Python, .NET, Java, Android signing tools and ADB
automatically, then opens the tool. No separate development-tool setup is needed.
The headset must have ADB enabled and authorize the PC for automatic APK access.
Alternatively, choose an APK file and use the tool without a connected headset.

This is an experimental local modification. Static verification and successful
packaging do not establish that startup or VR streaming works on a headset.
The initial code profiles were derived from version 1.34.25.0 (APK code 10722).

Using the tool
--------------
1. Run the release EXE. Choose "Extract installed APK using ADB" or "Use an APK
   file". Select the device serial if several headsets are connected.
2. Choose the installed VirtualDesktop.Streamer.exe if detection is ambiguous.
3. Click Prepare patches. It saves originals, checks both code profiles, builds
   and signs both outputs, and audits the APK. Installed apps stay intact.
4. Click Install session. Windows administrator access is required to change
   files in Program Files.

The install dialog includes "Disable Streamer updater". This optional checkbox
renames Updater.exe to Updater.exe.bak, records its hash, and refuses to overwrite
an existing backup. Restore originals reverses the rename after checking the
journal. A vendor installer or repair can recreate Updater.exe; this option
affects that file in this installation. The code profile also skips the checked
startup updater paths.

In ADB mode, a locally signed APK may require replacing the publisher-signed app.
Replacement is attempted only for Android's specific signature-mismatch error,
after explicit consent. By default, a verified root backup is required and
supported app data is restored after replacement. Allowing data loss is a
separate optional choice. Other installation errors do not trigger uninstall.
The connected headset must match the prepared session.

Manual APK mode never accesses ADB. Install session applies the PC side only;
install output/VirtualDesktop-local.apk on the headset yourself. Preserve app
data before replacing the publisher-signed app.

Restore originals uses the same session, checks saved hashes, preserves
unrelated Streamer settings, restores the original APK and available pre-patch
data, and reverses its updater rename. It refuses unknown app updates or
conflicting updater files.

Version compatibility
---------------------
Whole-file hashes identify and verify files; they are not the compatibility
rule. Streamer patch locations are resolved using contextual managed-code
signatures. APK methods are selected by type/method signatures and normalized
code fingerprints, including related async and discovery code. Matches must
be unique and the complete profile must pass before installation.

Unchanged method names alone are insufficient. Changed call flow, constants,
protocol structures, store format or runtime mode can require a new profile
or implementation. Layout/version-only differences can match an existing
profile. Missing or ambiguous matches stop the build. A match on another release
is a static result, not proof of runtime support. Use matching APK and Streamer
versions. Reviewed tool/profile updates can support changed releases.

State and recovery
------------------
The default work directory is %LOCALAPPDATA%/VirtualDesktopOffline. The launcher
keeps its extracted tools under tool-cache; identity contains persistent pairing
and APK signing keys; sessions contains originals, backups, reports and events.

Keep identity and sessions for future builds and recovery. Generated outputs may
be recreated; originals and recovery backups must remain intact. PC restore also
needs the backup and receipt in the Streamer installation. Never publish private
keys, session backups, generated APKs or pairing identities.

A completed session has output/build-report.json and no BUILD-INCOMPLETE.txt.
Outputs include VirtualDesktop.Streamer.exe, its patch report, the signed
VirtualDesktop-local.apk, and verification logs. Both apps must use the same
pairing identity. The identity is a signed bearer value, not hardware-bound.

Modification and limits
-----------------------
The Streamer keeps RSA-PSS verification and account comparison but trusts the
local pairing key. Its LAN listener starts without cloud computer registration.
Checked update paths, Internet probes, external traceroute and cloud retry are
skipped. The scoped executable self-check is adapted; publisher Authenticode
becomes invalid.

The APK supplies the corresponding account and signed discovery identity without
Meta entitlement/user-proof calls on the patched startup path. Empty proofs use
the existing local discovery path. Certificate guards are repinned to the local
APK certificate. Meta OS VR/OpenXR services remain required. Optional online
features are outside this modification.

Managed metadata identifiers, assembly-store indexes and native load segments
are preserved. Stale AOT companions are removed only for modified assemblies and
only when the packaged Mono runtime permits JIT fallback. The build checks
modified IL, unchanged APK members, assembly images, APK signatures and 16 KiB
alignment. Streamer IL verification is differential because the original
obfuscated executable already has verifier errors.

Runtime acceptance remains outstanding: cold startup without store/network
access, LAN discovery, video/audio, microphone, controllers, tracking, reconnect
and restart must be tested on the intended headset.

Source builds
-------------
End users need only the release EXE. Developers can build from a source checkout
with Build-Release.ps1 -OutputDirectory C:/path/new-release. This provisions the
dependencies, packages them, calls Build-Standalone.ps1 and verifies extraction.
The repository's Windows workflow automates the same build.
Pinned dependencies retain their included licenses and notices.

To publish a release: open GitHub Actions, select "Build and release Windows
EXE", click Run workflow, enter a new tag such as v0.1.0, and choose whether it
is a prerelease. Only a manual run publishes a release. Pushes and pull requests
build downloadable workflow artifacts. Existing release tags are not overwritten.

For a source checkout or extracted portable folder, Start.cmd launches the UI.
The command line is also available with runtime/python/python.exe:
  toolchain_cli.py scan
  toolchain_cli.py prepare --apk "C:/path/base.apk"
  toolchain_cli.py prepare --adb "C:/platform-tools/adb.exe"
  toolchain_cli.py install --session "C:/path/session" --confirm-reinstall --disable-updater
  toolchain_cli.py restore --session "C:/path/session" --confirm-reinstall

Optional preparation arguments: --streamer EXE, --serial SERIAL,
--state-directory DIR. Installation/recovery has separate --allow-data-loss and
--force-stop choices. Omit --confirm-reinstall in manual mode. Restore reads the
updater choice from the journal; --disable-updater applies only to installation.

Build-Patches.ps1 generates files without installation orchestration.
package_toolchain.py packages tools without application binaries or private
identity data. The older package_delivery.py was for a private prototype and
must not be used for public releases.

References
----------
https://github.com/Nuzair46/BlockTheSpot
https://github.com/dotnet/android/blob/main/Documentation/project-docs/AssemblyStores.md
https://developer.android.com/tools/adb
https://source.android.com/docs/security/features/apksigning

