# Virtual Desktop Offline Tool

A Windows tool that prepares a locally paired Virtual Desktop Streamer and Quest APK for offline use. Run one EXE, choose your files or connect your headset, and prepare the patches.

**No Virtual Desktop APK, Streamer installer, or Streamer executable is supplied. You must provide your own original files.** The tool can extract your installed APK from an authorized headset over ADB, or you can select an APK file yourself.

> **Experimental:** patch generation, signing, and file checks have passed. The modified apps have not yet been tested for startup or VR streaming on a headset. Keep your originals and recovery backups.

## What you need

- A Windows 10 or 11 PC running the 64-bit version of Windows.
- Your original Virtual Desktop Streamer installed on the PC.
- Your original Quest APK, either on a connected headset or saved as a file.
- Matching Streamer and APK versions.

The download includes the tools it needs, including ADB. You do not need to install Python, Java, a .NET SDK, or Android build tools separately.

For automatic headset installation, enable ADB access and authorize the PC on the headset. Preserving app data during replacement requires root access. The headset still needs its VR runtime services; the patch does not replace the headset OS.

## Get started

1. Download **VirtualDesktop-OfflineTool.exe** from this repository's **Releases** page and run it. If there is no release yet, a repository maintainer can create one using the workflow below.
2. The tool detects your Streamer and connected headsets automatically. Choose how to provide the APK:
   - **Use connected headset:** connect your headset and select it if several devices are listed.
   - **Use an APK file:** browse to your original APK. A connected headset is not required for this mode.
3. Use **Choose...** for your installed **VirtualDesktop.Streamer.exe** only if it was not detected.
4. Click **Prepare patches**. The tool saves the originals, checks compatibility, and builds both patched files. This step leaves your installed apps unchanged.
5. Click **Install** when you are ready. Windows will request administrator access to update the Streamer installation.

In manual APK mode, **Install** updates only the PC side. The generated **VirtualDesktop-local.apk** remains in the session's `output` folder for you to install yourself.

Saved sessions are selected automatically. ADB, device, working-folder, and session-folder overrides are available under **Advanced options**; normal use does not require them. **Show details** opens the full operation log.

## Installation options

### Disable the Streamer updater

Select **Disable Streamer updater** to rename `Updater.exe` to `Updater.exe.bak` during installation. The tool records its original hash and will not overwrite an existing backup. **Restore originals** reverses the rename.

A later vendor installation or repair may recreate `Updater.exe`.

### Preserve headset app data

The generated APK uses a local signing certificate. Android may require uninstalling the original app before installing it.

By default, automatic replacement requires a verified root backup and restores supported app data afterward. Allowing a clean installation with data loss is a separate option. The tool asks for replacement consent and does not uninstall the app for unrelated installation errors.

If you install the APK manually, preserve your headset data before replacing the original app.

## Which versions work?

The current patch profiles were developed against **Virtual Desktop 1.34.25.0**, with Quest APK version code **10722**.

The tool checks the relevant code rather than rejecting every file with a different hash. Another release may match the same profile, but unchanged method names alone do not guarantee compatibility. Missing, changed, or ambiguous matches stop preparation.

A successful profile match is not proof that every feature works on that release. Supporting a release with changed code may require an updated tool or profile. There is no option to force a patch onto an incompatible build.

## Restore the originals

Open the tool, select the same prepared session, and click **Restore originals**. It checks the saved files, restores the applicable originals and pre-patch headset data, removes its pairing settings, and reverses its updater rename.

After an interrupted installation, the action becomes **Recover originals**. Prepare and Install stay disabled until recovery finishes. The tool handles a temporarily uninstalled APK during replacement and transfers a verified archive file when restoring headset data.

Restore stops if an installed app or updater file has changed unexpectedly, so it does not overwrite an unrelated update.

Keep the session folder and the Streamer's backup and receipt files until you no longer need recovery.

## Where are my files?

By default, working files are saved in:

```text
%LOCALAPPDATA%\VirtualDesktopOffline
```

- `sessions` contains originals, generated patches, recovery backups, and logs.
- `identity` contains your persistent local pairing and APK signing key.
- `tool-cache` contains the tools unpacked from the EXE.

Each completed session has an `output` folder containing the patched Streamer, `VirtualDesktop-local.apk`, and `build-report.json`. Do not use outputs while `BUILD-INCOMPLETE.txt` is present.

Keep your signing keys, pairing identity, generated APKs, and headset backups private. Reuse the same identity for later local builds.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| Headset is missing or unauthorized | Enable ADB access, connect the headset, and accept its authorization prompt. You can also choose manual APK mode. |
| Several devices or Streamer installations are found | Select the device serial or Streamer executable explicitly. |
| Compatibility check fails | Keep the original files and check for a tool update that supports your version. |
| An updater backup already exists | Review the existing `Updater.exe.bak`; the tool will not overwrite it. |
| Data preservation is unavailable | Check root access. Choose a clean installation only if you accept losing app data. |
| Installation or recovery fails | Keep the session and backups, then inspect its logs before retrying. |

## Build and publish a release

Repository maintainers can generate the standalone EXE without uploading any APK or Streamer files:

1. Open **Actions → Build and release Windows EXE**.
2. Click **Run workflow** and enter a new tag, such as `v0.1.0`.
3. Choose whether to mark it as a prerelease, then run the workflow.

The workflow builds and verifies the EXE, then publishes it with a checksum and source archive. Only a manual run publishes a release. Pushes and pull requests build downloadable workflow artifacts. Existing releases are not overwritten.

To build locally from this codebase:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Build-Release.ps1 -OutputDirectory C:\path\new-release
```

The first source build downloads pinned dependencies. End users only need the resulting EXE.

## Current limits

The tool targets local pairing and the patched startup paths. Online features are outside its scope. Actual headset startup, LAN discovery, video, audio, tracking, controllers, reconnect, and restart still need runtime testing.

This is an unofficial modification and is not affiliated with Virtual Desktop or Meta. Bundled third-party tools retain their included licenses and notices.
