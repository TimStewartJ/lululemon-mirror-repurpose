# Getting started: stock Mirror to working appliance

This is the authoritative installation sequence for an owned, supported Mirror.
It keeps stock Android and the factory launcher installed; it does not flash an
OS, unlock the bootloader, or bypass ADB authorization. Follow the numbered
stages in order. Run commands individually, checking exit status and the
described result before continuing; these blocks are not an unattended script.
If a helper is installed, a failure means restore it, not leave it in place.

The application and recovery workflows have been exercised on the project's
IFC6309 device. **This guide has not been clean-room tested from an untouched
stock Mirror.** USB authorization, vendor Settings availability, and device-owner
eligibility can block a particular unit. Do not treat a build or an ADB install
success as proof that an unattended appliance is ready.

## 1. Confirm scope and prepare the computer

**Required hardware/firmware:** Qualcomm APQ8016/MSM8916, product `mirror`,
device `msm8916_64`, board platform `msm8916`, Android `6.0.1` (API 23),
build `IFC-6309-2.0-MIR`, build number `329`, with this exact fingerprint:

```text
mirror/mirror/msm8916_64:6.0.1/IFC-6309-2.0-MIR/329:user/release-keys
```

Later NXP i.MX8 units and other firmware are not supported. Stage 3 verifies
these properties over ADB; the chassis label alone is insufficient.

Use a trusted Windows computer, a data-capable USB cable, a visible Mirror
display, and a trusted private network. Keep physical USB access throughout.
The examples are PowerShell commands. Install Git, then choose a parent
directory for the checkout and run:

```powershell
git clone https://github.com/TimStewartJ/lululemon-mirror-repurpose.git
Set-Location .\lululemon-mirror-repurpose
```

Run the remaining repository commands from this directory. If you already
have a checkout, use it without overwriting local changes. These instructions
describe the code in the checkout; do not mix scripts or APKs from different
revisions.

| Requirement | Why |
|---|---|
| Python 3.9 or newer, callable as `python` | `tools\mirror.ps1` wraps the standard-library Python CLI; no pip packages are needed for installation. |
| JDK 17, including `java` and `keytool` | Recommended build JDK for the pinned Android Gradle plugin 8.8.2. |
| Android SDK Platform 35, Build Tools 35.0.0, Platform Tools | Compile SDK, `aapt`/`apksigner`, and `adb`. Install through Android Studio's SDK Manager or the official command-line tools; no NDK or emulator is needed. |
| Network access for the first build | The checked-in Gradle wrapper downloads Gradle 8.12 and build dependencies. A separate Gradle installation is unnecessary. |
| Private release signing key | Required for the release install path below and future same-key updates. |
| Browser | Mirror Home includes its own control website; nothing else is installed on the computer or phone to use it. |

Optional: scrcpy for no-touchscreen input (stage 2); OTA supervisor only
for unattended LAN APK updates (stage 8). Video rendering tools are not needed
to upload an existing MP4.

Configure `JAVA_HOME` to your JDK 17 directory and put its `bin` on `PATH`.
Set `ANDROID_SDK_ROOT` to your actual SDK directory and put its
`platform-tools` on `PATH`. Use the same ADB installation across tools.
For a nonstandard SDK location, also create an ignored `local.properties`
in the repository root for Gradle, for example:

```properties
sdk.dir=C:\\path\\to\\Android\\Sdk
```

Replace the example path. `local.properties` alone is not sufficient for the
Python build-tool lookup: it uses `ANDROID_SDK_ROOT`, `ANDROID_HOME`, or the
Windows default `%LOCALAPPDATA%\Android\Sdk`.

```powershell
Get-Command python, java, keytool, adb
python --version
java -version
```

**Expected:** real executables and the required versions. **Stop** for a missing
tool or incompatible SDK; do not install anything on the Mirror yet. See the
[AGP compatibility table](https://developer.android.com/build/releases/agp-8-8-0-release-notes)
for the pinned plugin's JDK/SDK requirements.
Android SDK downloads are available from the official
[Android Studio tools page](https://developer.android.com/studio).

## 2. Authorize USB without bypassing the prompt

Connect only the intended Android device and run:

```powershell
adb devices -l
```

**Expected:** one intended device in state `device`. If multiple devices must
remain connected, replace `DEVICE_SERIAL` with the intended identifier and
use `adb -s DEVICE_SERIAL ...` and
`.\tools\mirror.ps1 --serial DEVICE_SERIAL ...` throughout. Never commit serials
or unredacted command output.

If it says `unauthorized`, approve the RSA dialog on the physical display.
For a unit without a touchscreen, the supported *input attempt* is scrcpy AOA
keyboard control, not ADB key injection:

```powershell
winget install --exact Genymobile.scrcpy
```

Ensure `scrcpy` is on `PATH`. On Windows, an ADB daemon can block OTG USB
access. **`adb kill-server` disconnects all ADB sessions and forwards on the
computer.** Finish unrelated ADB work and close tools that restart the daemon.
Only after seeing the RSA dialog:

```powershell
adb kill-server
scrcpy --otg --keyboard=aoa --mouse=disabled
```

This mode uses **no ADB, video, or audio**. Focus its controller window but
watch the physical display. Navigate the visible dialog using `Tab` /
`Shift+Tab`, `Space`, and `Enter`; approve only the intended computer.
Choose **Always allow from this computer** only if you trust it. Never send
a fixed key sequence blindly. If the dialog disappears after stopping ADB,
stop OTG and re-establish the prompt; do not type approval keys into another
screen. Close OTG before restarting ADB:

```powershell
adb start-server
adb devices -l
```

**Stop** if the device is absent, `offline`, still `unauthorized`, or OTG cannot
open it. This project does not enable USB debugging on a stock device that
does not expose it. Resolve cables/drivers through the
[detailed authorization reference](provisioning.md#initial-adb-authorization-without-a-touchscreen)
and its official scrcpy links. Do not factory-reset, copy ADB keys, enable
wireless ADB, or enter EDL/fastboot to get around authorization.

## 3. Verify firmware and back up factory APKs

Read the [recovery precautions](recovery.md) before the first device write.
Never remove or disable `com.mirror.launcher`, and never reboot with a
transient helper update installed.

```powershell
adb shell getprop ro.build.fingerprint
.\tools\mirror.ps1 status
.\tools\mirror.ps1 backup
```

**Expected:** the exact fingerprint above; `status` reports profile
`ifc6309-mirror-329`; `backup` exits successfully and creates these files under
ignored `backups\ifc6309-mirror-329`:

- `co.mirror.datacap.apk`
- `com.mirror.launcher.apk`
- `com.mirror.services.apk`

The tool verifies their SHA-256 hashes against the
[device profile](../tools/device-profiles/ifc6309-mirror-329.json).
Privately copy the verified backups off the working computer. These are
**three factory APKs, not firmware/partition or user-data backups**. An
existing Mirror Home APK is backed up separately during an update; that is
not a backup of its settings or media either.

**Stop** on any profile/hash/pull error. Do not edit the profile to make an
unknown device pass. No compatible raw-flash recovery path is established.

## 4. Sign and install Mirror Home

The recommended path is an owner-signed source build, so you control future
updates and can sign the optional OTA supervisor with the same certificate.
Use a clean checkout of the intended release tag (for example, `v2.2.0`), not a
mixture of release scripts and unreleased code. The published-APK alternative
and its signing restrictions are described below.

For a first source installation, generate a private release key. If a key or
`keystore.properties` already exists, inspect and reuse the intended signing
configuration instead; **do not overwrite it**.

```powershell
New-Item -ItemType Directory -Force .secrets
keytool -genkeypair -v -keystore .secrets\mirror-home.jks `
  -alias mirror-home -keyalg RSA -keysize 4096 -validity 10000
Copy-Item keystore.properties.example keystore.properties
```

Edit the ignored `keystore.properties` locally: set `storeFile`,
`storePassword`, `keyAlias`, and `keyPassword` to the actual values. Relative
`storeFile` paths resolve from the repository root. Do not put passwords in
commands, documentation, or Git. Back up the key and passwords privately;
losing them prevents same-certificate updates.

```powershell
.\tools\mirror.ps1 install-home --variant release
```

This builds `android\mirror-home\build\outputs\apk\release\mirror-home-release.apk`,
installs it with `-r -g` (including runtime permission grants such as location
and camera), explicitly launches `MainActivity`, and checks the API/version
and activity. Camera motion sensing is optional; review it in **Schedule**.

**Expected:** `Mirror Home VERSION installed and health-checked successfully`
and a visible Mirror Home screen. The website is packaged in the APK.
**This has not yet selected default HOME.**

**Stop** on unsigned-release, certificate-mismatch, or health-check errors.
Do not uninstall a previous debug/release installation just to evade a
signing mismatch: that removes application data, pairings, and media.
Updates back up the installed APK and attempt rollback on a failed health
check; a failed first install has no previous Home APK and attempts removal
of the candidate. Unknown install state requires investigation, not retries
that assume rollback succeeded. See [Recovery](recovery.md#mirror-home-update-rollback).

### Published APKs and signing compatibility

A GitHub Release may include a project-signed Home APK, `SHA256SUMS.txt`, and
`build-provenance.json`. Check the downloaded file's SHA-256 against the sums
and verify its signature with Android SDK
`apksigner verify --min-sdk-version 23 --verbose --print-certs` (from Build Tools
35.0.0; use its full path if it is not on PATH). The certificate SHA-256 must
match the provenance. These checks verify the artifact, not the hardware:
complete stages 1-3 and retain authorized USB access.

An existing installation must keep its signing certificate. **Do not uninstall
Home, clear its data, generate a replacement key, or force a downgrade to use a
public APK with another certificate.** The installer above builds from source
using your private key; it does not install the downloaded APK. Project-signed
Home and an owner-signed OTA supervisor are not certificate-compatible. Build
both from source with your own key if you want owner-controlled OTA updates.

For a first installation only, after the profile/backups and artifact checks,
confirm `adb -s DEVICE_SERIAL shell pm path dev.mirror.repurpose` returns no
installed package. Then, replacing the placeholders with the intended device
and downloaded file:

```powershell
adb -s DEVICE_SERIAL install -r -g 'C:\path\to\mirror-home-2.2.0.apk'
adb -s DEVICE_SERIAL shell am start -n dev.mirror.repurpose/.MainActivity
```

Require install `Success`, a successful activity start, and a visible Home
screen. This manual path does not provide `install-home`'s transactional health
check or rollback. Stop on any error and use the recovery guide; do not remove
or disable the factory launcher. Continue stages 5-8 to pair, configure Wi-Fi,
select default HOME and verify reboot persistence. APK installation alone is
not a completed installation, and this path has not been clean-room validated.

Public 2.2.0 uses version code 76; public 2.1.0 used 71. A Mirror that runs an
earlier public APK is updated by installing the newer one over it with the
same `install -r -g` command, which keeps its pairings, settings and media.
The OTA supervisor requires a strictly increasing version code and never
accepts another build of the installed one. Public APKs leave test health
failures and unattended background-video bootstrap provisioning disabled;
normal on-glass pairing is still required.

## 5. Pair a browser and configure Wi-Fi

Keep USB attached. Open the Mirror-hosted page through a local forward:

```powershell
.\tools\mirror.ps1 forward
```

**Expected:** `http://127.0.0.1:18787`. Open that URL on the computer, enter
the current six-digit code from the glass, and give the browser a recognizable
name. This is separate from ADB RSA authorization. Do not share pairing codes
or the resulting browser credential.

Alternatively, if Android already has a Wi-Fi address, scan the control QR.
With neither an active Wi-Fi connection nor an app-managed SSID, the app
attempts a Wi-Fi Direct setup group after startup: scan **Join Mirror Setup
Wi-Fi**, stay on that network despite its lack of Internet, then scan **Open
setup** and pair. Use the displayed address rather than guessing it.

**Safe stop:** if the page cannot open, check that Home is running and
recreate the forward; do not expose ADB over the network. With neither a LAN
connection nor a working setup group, the native screen shows **Ready to set
up** with the pairing code, and the USB-forwarded page above is the way to
pair. The Mirror accepts a code only while it is showing one; this guide does
not provide a pairing bypass.

In the paired browser:

1. Open **Settings > Wi-Fi**. Type the exact network name and password,
   check **Hidden network** only if needed, then choose **Connect**.
   There is no network-scanning picker. The implementation is WPA-PSK;
   use a compatible personal network, not an open, enterprise, captive-portal,
   or WPA3-only network.
2. Wait for an actual Wi-Fi address and verify access to
   `http://MIRROR_IP:8787/` from a browser on that same LAN. `MIRROR_IP` means
   the address shown in Settings, not a literal hostname.
3. The page redirects with its credential in a URL fragment when the API
   returns an address; the destination removes the fragment after loading.
   A phone on setup Wi-Fi must rejoin the household network. If the redirect
   fails, keep USB access, return to the forwarded page, and check status.
4. Set the Mirror's name and clock preferences in **Settings**. On **Display**,
   select **Mirror** under **What the mirror shows**.

**Expected:** household SSID/address, a paired LAN browser, and the built-in
dashboard on the glass. A message saying **Wi-Fi connection requested** is
not proof of successful association. Do not remove USB until LAN access works.
The setup group is not a guaranteed fallback for a previously managed SSID
that becomes unreachable; use the paired USB page's **Settings > Setup
network > Start** when necessary.

## 6. Select persistent HOME

**Required outcome, optional mechanism:** Mirror Home must be the preferred
Android HOME activity. Installing/launching it does not set this preference.
Its boot receiver starts the service and watchdog; it does not unconditionally
launch the dashboard on boot.

First try the ordinary Android HOME selection UI:

```powershell
adb shell am start -a android.settings.HOME_SETTINGS
```

If the vendor Settings screen is available, select **Mirror Home**. A HOME
chooser may instead appear when requesting HOME; choose Mirror Home and
**Always**, not **Just once**. For no-touchscreen input, close any OTG session
and use ordinary authorized scrcpy from a second terminal:

```powershell
scrcpy --no-audio
```

This uses ADB video/control, unlike stage 2's OTG mode; audio forwarding is
unavailable on Android 6. Select only what you can see and close scrcpy when
finished. See [official scrcpy usage](https://github.com/Genymobile/scrcpy#prerequisites).
The
[Android 6.0.1 Settings manifest](https://github.com/aosp-mirror/platform_packages_apps_settings/blob/android-6.0.1_r1/AndroidManifest.xml)
defines the HOME settings action, but that does not prove this vendor image
exposes a usable selection screen.

```powershell
adb shell am start -a android.intent.action.MAIN -c android.intent.category.HOME
```

**Expected:** Mirror Home opens without a chooser. If Settings is missing or
stock HOME still opens, do not disable the stock launcher. Either stop here
with a manually launchable app, or explicitly choose the optional helper
below. Do not substitute a modern Android `set-home-activity` recipe for
this Android 6 workflow.

### Optional transient helper

Only use this on the verified profile with the stage 3 backups and continuous
USB/power access. It temporarily replaces the `co.mirror.datacap` update with
a guarded UID-1000 payload. **Installation alone changes neither HOME nor
kiosk settings. Restore the factory APK before any reboot, even on failure.**

The helper is driven through Mirror Home's API. In the paired browser, open
**Settings > Paired devices** and choose **Show code**. Pair this temporary
setup client using that code, keeping the credential only in PowerShell memory:

```powershell
.\tools\mirror.ps1 forward
$api = 'http://127.0.0.1:18787/api/v1'
$pairBody = @{ code = (Read-Host 'Code from Show code'); name = 'Temporary HOME setup' } | ConvertTo-Json
$pair = Invoke-RestMethod -Method Post -Uri "$api/pair" -ContentType 'application/json' -Body $pairBody
$headers = @{ Authorization = "Bearer $($pair.token)" }
.\tools\mirror.ps1 install-helper
```

**Expected:** the helper CLI confirms installation for this boot only. It
checks source hashes/manifest, binds to the installed Home certificate, and
saves the three original kiosk settings in
`backups\ifc6309-mirror-329\kiosk-settings.json`.

```powershell
Invoke-RestMethod -Uri "$api/system" -Headers $headers
Invoke-RestMethod -Method Post -Uri "$api/system/home" -Headers $headers `
  -ContentType 'application/json' -Body '{"enabled":true}'
```

Require `connected: true` (retry the status query if binding is still starting)
and `changed: true`, `enabled: true`. A 503 or false result is a failure.
Optionally apply persistent stay-awake, maximum screen timeout, and immersive
confirmation settings; these are separate from HOME selection:

```powershell
Invoke-RestMethod -Method Post -Uri "$api/system/prepare-kiosk" -Headers $headers
```

Require `prepared: true` if you chose that operation. Whether operations
succeed or fail, remove the helper update **now**:

```powershell
.\tools\mirror.ps1 restore-helper
adb shell pm path co.mirror.datacap
```

Use `restore-helper --keep-kiosk-settings` **instead** only if intentionally
retaining the optional kiosk changes. Neither form reverses preferred HOME.
The only acceptable package path before reboot is:

```text
package:/system/app/co.mirror.datacap/co.mirror.datacap.apk
```

If restoration fails or the path remains under `/data/app`, **do not reboot**;
follow [helper recovery](recovery.md#helper-restoration-before-reboot).
After restoration, repeat the generic HOME request above. Revoke **Temporary
HOME setup** in browser **Settings > Paired devices** and close the setup
PowerShell session.

## 7. Verify the appliance across reboot

Even if you skipped the helper, verify the active factory package path:

```powershell
adb shell pm path co.mirror.datacap
```

Require exactly
`package:/system/app/co.mirror.datacap/co.mirror.datacap.apk`.
Stop and use [helper recovery](recovery.md#helper-restoration-before-reboot)
if it is absent or points under `/data/app`. Only after this check and a
successful generic HOME request:

```powershell
adb reboot
```

Wait for Android to finish booting; do not issue an explicit `MainActivity`
launch to make this test pass. **Expected:** Mirror Home appears without
intervention, Wi-Fi reconnects, the paired LAN browser still works, and name,
layout, and clock preferences remain. If not, stop and use
[Recovery](recovery.md); an API-only service response is not dashboard
persistence. Keep USB recovery available until this test succeeds.

For a first media check, in the browser choose **Display > Background
videos > Upload MP4** and select a small H.264 MP4 you own. No sample film
is bundled. Browser upload validates and **automatically activates** it;
this also switches away from a custom web dashboard to the built-in layout.
Use **Fill** or **Whole** and dimming in the background editor. Verify the
loop on the glass, then reload the browser and confirm the active library
card. Reboot again if you want to verify your media selection's persistence.

Limits: 256 MiB per file, one H.264 track, up to 1080x1920 portrait or
1920x1080 landscape, 30 FPS and 20 Mbps, 1 second to 6 hours, and a
hardware-supported AVC profile/level. The library permits 12 videos/768 MiB
with a 512 MiB free-space reserve. Audio is ignored. On rejection, retain the
current background and correct the file; do not raise limits or clear app
data. A successful upload with failed activation may leave an inactive card;
choose **Use** to retry. See [Background videos](background-videos.md).

For full-screen playback instead, use **Home** to play a reachable media URL;
see [Streaming](streaming.md). Daily use needs no computer. Keep
these HTTP controls on a trusted LAN; never router-forward ports `8787`,
`8791`, or ADB.

## 8. Optional LAN OTA enrollment

Skip this if same-key USB updates are sufficient. OTA is a separate
device-owner app, not a prerequisite for HOME persistence or browser/video
control. It does not select HOME.

With a working, release-signed Home, verified firmware, saved signing key,
LAN connectivity, and USB recovery, follow
[LAN OTA updates](ota-updates.md#one-time-provisioning). Build the supervisor
with the same release key, install it, explicitly enroll it as device owner,
provision its independent credential, and verify authenticated LAN status.
Stop if Android rejects enrollment; do not remove accounts/users, reset the
device, or alter setup flags to force it.

## Completion and ongoing use

The installation is ready only when firmware/backup checks, a signed Home
install, visible default HOME after reboot, saved Wi-Fi, and a paired LAN
browser have all passed. Record outcomes privately; do not commit identifiers,
credentials, backups, or media. Optional video and OTA checks should be
recorded separately, not assumed from installation.

- [User guide](user-guide.md): installed-appliance controls and daily use.
- [Provisioning reference](provisioning.md): USB authorization and Wi-Fi.
- [Recovery](recovery.md): factory helper, stock HOME, and APK rollback.
- [Supported devices](supported-devices.md): exact hardware boundary.
