# Provisioning

This is the detailed USB/Wi-Fi reference, not an installation checklist.
For a stock unit, start with [Getting started](getting-started.md), including
release signing, factory backups, default HOME selection, and reboot checks.
For daily use after installation, see the [User guide](user-guide.md).

## Initial ADB authorization without a touchscreen

Use this path when the owned Mirror already exposes an ADB USB interface
but `adb devices -l` reports `unauthorized`, and you cannot approve the
computer's RSA key by touch. This project does not enable USB debugging,
install ADB keys, or bypass Android's authorization prompt.

scrcpy's USB OTG mode can present the computer keyboard to Android through
AOAv2 HID without using ADB. It requires compatible device firmware and USB
drivers; it does not establish that a Mirror is supported by this project.
Verify the [supported profile](supported-devices.md) after authorization.

The following examples use PowerShell on Windows. Run repository commands
from the repository root and connect only the intended Android device. If
other devices must stay connected, select the Mirror explicitly with
`adb -s SERIAL ...`, `scrcpy --otg -s SERIAL ...`, and
`.\tools\mirror.ps1 --serial SERIAL status`, replacing `SERIAL` with its
actual identifier. Do not commit device serials.

1. Install scrcpy using its official
   [Windows installation instructions](https://github.com/Genymobile/scrcpy/blob/master/doc/windows.md).
   For example:

   ```powershell
   winget install --exact Genymobile.scrcpy
   ```

   Make both `adb` and `scrcpy` available on `PATH`; reopen the terminal
   after installation if needed. Check with `Get-Command adb, scrcpy`.
   If using an existing Android SDK, its ADB executable is commonly at
   `$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe`; use that SDK's
   actual location rather than assuming this path exists. Keep one ADB
   version in use across your tools.

2. Connect the Mirror using a data-capable USB cable, leave its display on,
   and request the authorization prompt:

   ```powershell
   adb devices -l
   ```

   Confirm that the intended device is `unauthorized` and that the RSA
   approval dialog is visible on the physical display. If it already shows
   `device`, skip to step 6. If it is absent or `offline`, resolve the
   cable, driver, or ADB connection issue first.

3. On Windows, the ADB daemon can prevent scrcpy from opening the USB
   device. **Stopping it disconnects all ADB sessions and forwards on this
   computer**, not just the Mirror. Finish other ADB work and close tools
   that automatically restart the daemon before continuing:

   ```powershell
   adb kill-server
   scrcpy --otg --keyboard=aoa --mouse=disabled
   ```

   OTG mode intentionally has no video or audio. Its window is a keyboard
   controller, not a mirrored view of Android.

4. Focus the scrcpy OTG window and watch the physical Mirror display.
   Use `Tab` or `Shift+Tab` to navigate the visible approval dialog,
   `Space` to toggle its checkbox, and `Enter` to activate the focused
   button. Select **Always allow from this computer** only for a computer
   you trust, then select **Allow**. Do not send a fixed sequence blindly:
   initial focus and button order can vary. If the prompt disappeared when
   ADB stopped, do not send approval keystrokes to another screen; stop OTG
   and re-establish the prompt before trying again.

5. Close scrcpy OTG, restart ADB, and verify that the state is now `device`:

   ```powershell
   adb start-server
   adb devices -l
   ```

6. Before installing anything, verify the exact supported profile:

   ```powershell
   adb shell getprop ro.build.fingerprint
   .\tools\mirror.ps1 status
   ```

   `status` checks product, device, board platform, Android release, build
   ID, and fingerprint against the configured profile and refuses a
   mismatch. Do not treat a USB VID/PID, a chassis label, or a stock
   network service as proof of compatibility.

If scrcpy cannot find or open the USB device, consult its official
[Windows OTG troubleshooting](https://github.com/Genymobile/scrcpy/blob/master/FAQ.md#otg-issues-on-windows)
before changing drivers. If the device remains `unauthorized`, do not copy
ADB keys, enable wireless ADB, factory-reset the unit, or use EDL/fastboot as
an authorization shortcut. Use a documented manufacturer or owner-controlled
recovery path instead. See the official scrcpy
[OTG](https://github.com/Genymobile/scrcpy/blob/master/doc/otg.md) and
[keyboard](https://github.com/Genymobile/scrcpy/blob/master/doc/keyboard.md)
documentation for platform requirements.

## QR and Wi-Fi Direct

Once Mirror Home is installed, its normal Wi-Fi onboarding path does not
require ADB:

1. With neither a managed SSID nor an active Wi-Fi connection, Mirror Home
   attempts to create a private Wi-Fi Direct group after startup.
2. Scan the first QR code to join it.
3. Scan the second QR code to open the displayed setup address (normally
   `http://192.168.49.1:8787/`).
4. Pair with the six-digit code and submit household Wi-Fi credentials.
5. The browser transfers its credential to the new LAN origin in a URL fragment
   and removes the fragment immediately after loading.

When Wi-Fi is already connected and the native setup screen is shown, scan
the single control QR code. After pairing, the built-in dashboard replaces
that screen. To pair another client, choose **Settings > Paired devices >
Show code** in an already-paired browser, then open the Mirror's LAN control
address on the new client and enter that code.

Automatic setup-group creation is not a general disconnected-network fallback:
a saved app-managed SSID prevents it even if that network is now unreachable.
Use the paired controls' **Settings > Setup network > Start** via USB when
needed. If both Wi-Fi and group creation fail, the native screen still shows
the pairing code, so a USB-forwarded browser can pair. See the
[first-pairing safe stop](getting-started.md#5-pair-a-browser-and-configure-wi-fi).

## USB access and recovery

Once [Mirror Home is installed](getting-started.md#4-sign-and-install-mirror-home)
and running, authorize ADB as above, then:

```powershell
.\tools\mirror.ps1 forward
```

Open the printed `http://127.0.0.1:18787/` on the computer. A different local
port can be chosen with `forward --host-port 18788`; open the printed URL.
This is a transport, not authentication or installation. Recreate the forward
after ADB restarts or USB reconnects. If the page cannot load, check the
connection and launch the installed Home explicitly:

```powershell
adb shell am start -n dev.mirror.repurpose/.MainActivity
```

Launching explicitly is useful for recovery but does not set default HOME or
prove reboot persistence. See [Recovery](recovery.md) for failed installs.

Pair in the forwarded Mirror-hosted page; the browser sends its device
credential to the forwarded API.

## Browser Wi-Fi configuration

In the paired page, use **Settings > Wi-Fi**: type the SSID and password,
optionally mark **Hidden network**, then **Connect**. There is no SSID scan
picker. The Android 6 implementation configures WPA-PSK personal networks;
open, enterprise, captive-portal, and WPA3-only provisioning are not implemented.
The SSID must be 1-32 UTF-8 bytes, and the passphrase 8-63 bytes or 64 hex
digits. Location permission is required; the installer uses `adb install -g`
to grant runtime permissions.

The response can report **connection requested** before association succeeds.
Check the actual SSID/IP and reach the LAN page before disconnecting USB.
If Android is already connected to that SSID, the app adopts it as managed
without changing its password. For an existing network Android will not let
the app remove, it attempts reactivation rather than replacing credentials.
Investigate a failed connection instead of assuming the submitted password
was applied.

When an address is returned, the page redirects there, including from a USB
forwarded page. A phone still on setup Wi-Fi must return to the household
network. The setup group is scheduled to stop after an accepted request, so
do not rely on it remaining available after a wrong password. Keep USB access
and the original paired browser available for recovery. Do not copy the
credential-bearing handoff URL into messages or logs.

## LAN and discovery

After Wi-Fi provisioning, the browser moves to the Mirror's private LAN address
with the same bearer token. The Mirror-hosted control page uses named
per-browser device credentials.

Use the address reported by the Mirror, for example `http://MIRROR_IP:8787/`
with `MIRROR_IP` replaced, not a saved address from someone else's unit.
The app advertises `_mirror-home._tcp` and `_http._tcp`; discovery does not
authorize a client. Keep the legacy Android device and its HTTP controls on a
trusted LAN without router port forwarding.
