# Virtual Desktop Offline Tool

Prepare and install offline patches for your Virtual Desktop Streamer and Quest app from one Windows EXE.

**[Download the Windows EXE](https://github.com/Nuzair46/fck-VirtualDesktop/releases/download/v0.1.0-beta.2/VirtualDesktop-OfflineTool.exe)** · [All releases](https://github.com/Nuzair46/fck-VirtualDesktop/releases)

ADB and the other required tools are included. You do not need to install Python, Java, or build tools.

**No Virtual Desktop APK, Streamer installer, or Streamer executable is supplied. You must provide your own originals.** The tool can copy the APK from your connected headset or use an APK file you select.

Patched apps show **`fkvd`** after the version number in both the Streamer and headset menu. This release includes the fix for the invisible headset menu in the first beta.

## Before you start

- Use a Windows 10 or 11 PC with 64-bit Windows.
- Install your original Virtual Desktop Streamer on the PC.
- Have the matching original Quest app on your headset, or an original APK file.
- For automatic headset setup, enable ADB access and authorize this PC on the headset. Root access is required to preserve app data when the app must be replaced.

The headset still needs its VR runtime services. This tool does not replace the headset OS.

## Set up with a connected headset

1. **Download and run `VirtualDesktop-OfflineTool.exe`.** Choose the EXE under the release's **Assets**; the source ZIP is for developers. First launch takes a little longer while the included tools are unpacked.
2. **Connect your headset and accept its ADB authorization prompt.** Leave **Use connected headset** selected. Click **Refresh** if needed, and select your headset if more than one is connected.
3. **Check the detected Streamer.** If it is missing, click **Choose...** and select your installed `VirtualDesktop.Streamer.exe`.
4. **Click Prepare patches and wait for it to finish.** This checks compatibility and saves the originals. Your installed apps are unchanged at this stage.
5. **Click Install.** Review the replacement and app-data choices, then approve the Windows administrator prompt. Keep the headset connected until the operation finishes.

Leave **Preserve app data** selected if you want to keep your data. This needs a verified root backup; without one, installation stops. Choose a clean installation only if you accept losing the existing app data.

**Unrooted headset?** You can still extract, patch, and install through ADB. Select **Allow a clean headset installation and loss of existing app data** when installing. Only Virtual Desktop's app data is removed; other apps are unaffected. You can restore the original APK later, but app data without a backup cannot be recovered.

You can also select **Disable Streamer updater** in the installation dialog. This renames `Updater.exe` to `Updater.exe.bak`; restoring the originals reverses it. A later vendor installation or repair may recreate the updater.

## Have an APK file instead?

Select **Use an APK file**, browse to your original APK, then follow steps 3–5 above. No connected headset or separate ADB download is needed to prepare the files.

In this mode, **Install updates the PC Streamer only**. Install the generated `VirtualDesktop-local.apk` on the headset yourself. It is in the session's `output` folder, under:

```text
%LOCALAPPDATA%\VirtualDesktopOffline\sessions
```

Replacing the original APK may require uninstalling it because the signing certificate changes. Back up your headset app data first.

## Undo changes or recover after an error

Open the tool, select the session you used, and click **Restore originals**. For an interrupted installation, the button reads **Recover originals**.

Keep the headset connected if that session used it. Recovery restores the saved originals and, when available, the pre-install app-data backup. Once recovery finishes, click **Prepare patches** to start a fresh session before trying again.

**Keep your session folder, signing keys, and Streamer backup files.** They are needed for recovery and later builds. Restore stops if files have changed unexpectedly.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| Headset missing or unauthorized | Check the connection and ADB access, accept the headset prompt, then click **Refresh**. |
| Streamer not detected | Click **Choose...** and select `VirtualDesktop.Streamer.exe`. |
| Compatibility check failed | Check for a tool update. Matching method names alone do not guarantee a version is supported. |
| App-data backup unavailable | Check headset root access, or explicitly choose a clean installation if data loss is acceptable. |
| Recovery needed or `recovery_required` | Click **Recover originals**. After recovery, prepare a new session. |
| Invisible menu with an older build | Restore the originals, then prepare and install fresh patches with the latest tool. |
| `Cannot query installed APK paths` in an older build | Close the old tool, run this release, select the affected session, and click **Recover originals**. |
| Another error | Enable **Show details** to read the full log. Keep the session and backups. |

Normal use does not need **Advanced options**. It contains overrides for ADB, devices, and working folders.

## Supported versions

The current profiles were developed against **Virtual Desktop 1.34.25.0**, with Quest APK version code **10722**. Other versions may work only if all compatibility checks pass. Changed or ambiguous code stops preparation; there is no force-patch option.

## Updating from an older tool

Use **Restore originals** for the installed session first. Then open the latest EXE, click **Prepare patches**, and install the new session. Keep your existing working folder to reuse the pairing identity, signing key, and recovery backups.

<details>
<summary>File locations and backups</summary>

The default working folder is `%LOCALAPPDATA%\VirtualDesktopOffline`:

- `sessions`: original files, patched outputs, recovery backups, and logs.
- `identity`: your local pairing identity and APK signing key. Reuse these for later builds.
- `tool-cache`: tools unpacked from the EXE.

Keep signing keys, generated APKs, and headset backups private. Do not use session outputs while `BUILD-INCOMPLETE.txt` is present.

</details>

<details>
<summary>For maintainers: build and publish</summary>

To publish a release, open [Actions: Build and release Windows EXE](https://github.com/Nuzair46/fck-VirtualDesktop/actions/workflows/build-windows.yml), click **Run workflow**, enter a new version tag, and choose whether it is a prerelease. The workflow builds and checks the EXE, then uploads it with a checksum and source archive. No APK or Streamer upload is required. Existing releases are not overwritten.

Ordinary pushes and pull requests only build workflow artifacts.

To build locally, choose a new output directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Build-Release.ps1 -OutputDirectory C:\path\new-release
```

The first source build downloads pinned dependencies. End users only need the resulting EXE.

</details>

## License

This codebase is available under the [MIT license](LICENSE). Virtual Desktop application files are supplied by the user and are not covered by this license. Bundled third-party tools retain their own licenses and notices.

This is an unofficial modification and is not affiliated with Virtual Desktop or Meta.
