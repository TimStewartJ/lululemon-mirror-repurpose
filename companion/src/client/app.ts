import { companionApi, type DiscoveredDevice } from './api.js';
import { getStoredToken, setStoredToken } from './token.js';
import { provisionOverBluetooth, isWebBluetoothSupported } from './ble.js';

function byId<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing element #${id}`);
  return el as T;
}

function setStatus(el: HTMLElement, message: string, isError = false): void {
  el.textContent = message;
  el.classList.toggle('error', isError);
}

async function guard(el: HTMLElement, action: () => Promise<string>): Promise<void> {
  try {
    setStatus(el, await action());
  } catch (error) {
    setStatus(el, (error as Error).message, true);
  }
}

// --- Companion access token --------------------------------------------------

const accessTokenStatusEl = byId<HTMLParagraphElement>('access-token-status');
const accessTokenInput = byId<HTMLInputElement>('access-token');
accessTokenInput.value = getStoredToken();

byId<HTMLFormElement>('access-token-form').addEventListener('submit', (event) => {
  event.preventDefault();
  setStoredToken(accessTokenInput.value.trim());
  setStatus(accessTokenStatusEl, 'Token saved for this browser tab.');
});

byId<HTMLButtonElement>('clear-access-token').addEventListener('click', () => {
  accessTokenInput.value = '';
  setStoredToken('');
  setStatus(accessTokenStatusEl, 'Token cleared.');
});

// --- Device discovery & connection ---------------------------------------

const deviceListEl = byId<HTMLUListElement>('device-list');
const connectStatusEl = byId<HTMLParagraphElement>('connect-status');

function renderDevices(devices: DiscoveredDevice[]): void {
  deviceListEl.innerHTML = '';
  if (devices.length === 0) {
    const li = document.createElement('li');
    li.textContent = 'No ADB devices found. Check the USB cable and driver.';
    deviceListEl.appendChild(li);
    return;
  }
  for (const device of devices) {
    const li = document.createElement('li');
    li.className = device.supported ? '' : 'unsupported';

    const label = document.createElement('span');
    label.textContent = `${device.serial} (${device.state})`;

    const pill = document.createElement('span');
    pill.className = `pill ${device.supported ? 'ok' : 'bad'}`;
    pill.textContent = device.supported ? 'supported' : 'unsupported';
    pill.title = device.reasons.join('; ');

    li.append(label, pill);

    if (device.supported) {
      const button = document.createElement('button');
      button.textContent = 'Connect';
      button.addEventListener('click', () =>
        guard(connectStatusEl, async () => {
          const result = await companionApi.connectDevice(device.serial);
          return `Connected on loopback port ${result.localPort}.`;
        }),
      );
      li.appendChild(button);
    }

    deviceListEl.appendChild(li);
  }
}

byId<HTMLButtonElement>('refresh-devices').addEventListener('click', () =>
  guard(connectStatusEl, async () => {
    const { devices } = await companionApi.listDevices();
    renderDevices(devices);
    return `Found ${devices.length} device(s).`;
  }),
);

byId<HTMLButtonElement>('disconnect-device').addEventListener('click', () =>
  guard(connectStatusEl, async () => {
    await companionApi.disconnectDevice();
    return 'Disconnected.';
  }),
);

// --- Pairing ---------------------------------------------------------------

const pairStatusEl = byId<HTMLParagraphElement>('pair-status');

byId<HTMLFormElement>('pair-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const code = byId<HTMLInputElement>('pair-code').value.trim();
  void guard(pairStatusEl, async () => {
    await companionApi.pair(code);
    return 'Paired successfully.';
  });
});

byId<HTMLButtonElement>('revoke-pairing').addEventListener('click', () =>
  guard(pairStatusEl, async () => {
    await companionApi.revokePairing();
    return 'Pairing revoked.';
  }),
);

// --- Status dashboard --------------------------------------------------------

function setField(name: string, value: string): void {
  const el = document.querySelector(`[data-field="${name}"]`);
  if (el) el.textContent = value;
}

async function refreshStatus(): Promise<void> {
  const status = (await companionApi.deviceStatus()) as {
    paired?: boolean;
    displayName?: string;
    brightness?: number | null;
    wifi?: { connected?: boolean; ssid?: string };
  };
  setField('paired', status.paired ? 'yes' : 'no');
  setField('displayName', status.displayName ?? '—');
  setField('brightness', status.brightness == null ? '—' : String(status.brightness));
  setField(
    'wifi',
    status.wifi?.connected ? `connected (${status.wifi.ssid ?? 'unknown SSID'})` : 'disconnected',
  );
}

byId<HTMLButtonElement>('refresh-status').addEventListener('click', () => {
  void refreshStatus().catch(() => {
    /* status card silently stays at its last known values on failure */
  });
});

// --- Wi-Fi form --------------------------------------------------------------

const wifiStatusEl = byId<HTMLParagraphElement>('wifi-status');

byId<HTMLFormElement>('wifi-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const ssid = byId<HTMLInputElement>('wifi-ssid').value.trim();
  const passphraseInput = byId<HTMLInputElement>('wifi-passphrase');
  const passphrase = passphraseInput.value;
  const hidden = byId<HTMLInputElement>('wifi-hidden').checked;

  void guard(wifiStatusEl, async () => {
    const result = await companionApi.configureWifi(ssid, passphrase, hidden);
    // The passphrase is never retained client-side either, once submitted.
    passphraseInput.value = '';
    return result.message;
  });
});

// --- Dashboard URL / name / brightness --------------------------------------

const dashboardStatusEl = byId<HTMLParagraphElement>('dashboard-status');

byId<HTMLFormElement>('dashboard-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const url = byId<HTMLInputElement>('dashboard-url').value.trim();
  void guard(dashboardStatusEl, async () => {
    await companionApi.setDashboardUrl(url);
    return 'Dashboard URL saved.';
  });
});

byId<HTMLFormElement>('name-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const name = byId<HTMLInputElement>('display-name').value.trim();
  void guard(dashboardStatusEl, async () => {
    await companionApi.setName(name);
    return 'Display name saved.';
  });
});

const brightnessInput = byId<HTMLInputElement>('brightness-value');
const brightnessOutput = byId<HTMLOutputElement>('brightness-output');
brightnessInput.addEventListener('input', () => {
  brightnessOutput.textContent = brightnessInput.value;
});

byId<HTMLFormElement>('brightness-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const value = Number.parseInt(brightnessInput.value, 10);
  void guard(dashboardStatusEl, async () => {
    await companionApi.setBrightness(value);
    return `Brightness set to ${value}.`;
  });
});

// --- Media library -----------------------------------------------------------

const mediaListEl = byId<HTMLUListElement>('media-list');
const mediaStatusEl = byId<HTMLParagraphElement>('media-status');

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unitIndex = -1;
  do {
    value /= 1024;
    unitIndex += 1;
  } while (value >= 1024 && unitIndex < units.length - 1);
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

async function refreshMedia(): Promise<void> {
  const { entries } = await companionApi.listMedia();
  mediaListEl.innerHTML = '';
  for (const entry of entries) {
    const li = document.createElement('li');

    const name = document.createElement('span');
    name.textContent = entry.name;

    const size = document.createElement('span');
    size.textContent = formatBytes(entry.sizeBytes);

    const playButton = document.createElement('button');
    playButton.textContent = 'Play';
    playButton.addEventListener('click', () =>
      guard(mediaStatusEl, async () => {
        await companionApi.playHostedMedia(entry.name);
        return `Playing ${entry.name} on the Mirror.`;
      }),
    );

    const stopButton = document.createElement('button');
    stopButton.className = 'secondary';
    stopButton.textContent = 'Stop';
    stopButton.addEventListener('click', () =>
      guard(mediaStatusEl, async () => {
        await companionApi.stopMedia();
        return 'Playback stopped.';
      }),
    );

    const deleteButton = document.createElement('button');
    deleteButton.className = 'secondary';
    deleteButton.textContent = 'Delete';
    deleteButton.addEventListener('click', () =>
      guard(mediaStatusEl, async () => {
        await companionApi.deleteMedia(entry.name);
        await refreshMedia();
        return `Deleted ${entry.name}.`;
      }),
    );

    li.append(name, size, playButton, stopButton, deleteButton);
    mediaListEl.appendChild(li);
  }
}

byId<HTMLButtonElement>('refresh-media').addEventListener('click', () =>
  guard(mediaStatusEl, async () => {
    await refreshMedia();
    return 'Media list refreshed.';
  }),
);

byId<HTMLFormElement>('upload-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const fileInput = byId<HTMLInputElement>('upload-file');
  const file = fileInput.files?.[0];
  if (!file) return;
  void guard(mediaStatusEl, async () => {
    await companionApi.uploadMedia(file);
    fileInput.value = '';
    await refreshMedia();
    return `Uploaded ${file.name}.`;
  });
});

// --- System helper -------------------------------------------------------------

const systemStatusEl = byId<HTMLParagraphElement>('system-status');

async function refreshSystemStatus(): Promise<void> {
  const status = await companionApi.systemStatus();
  setField('systemConnected', status.connected ? 'yes' : 'no');
  setField('systemCapabilities', status.capabilities?.length ? status.capabilities.join(', ') : '—');
}

byId<HTMLButtonElement>('refresh-system').addEventListener('click', () =>
  guard(systemStatusEl, async () => {
    await refreshSystemStatus();
    return 'System status refreshed.';
  }),
);

byId<HTMLButtonElement>('prepare-kiosk').addEventListener('click', () =>
  guard(systemStatusEl, async () => {
    const result = await companionApi.prepareKiosk();
    return result.prepared ? 'Kiosk mode prepared.' : 'Kiosk mode preparation failed on-device.';
  }),
);

byId<HTMLButtonElement>('use-mirror-home').addEventListener('click', () =>
  guard(systemStatusEl, async () => {
    const result = await companionApi.setSystemHome(true);
    return result.changed
      ? 'Mirror Home is now the default launcher.'
      : 'Mirror Home was already the default launcher.';
  }),
);

byId<HTMLButtonElement>('restore-stock-home').addEventListener('click', () =>
  guard(systemStatusEl, async () => {
    const result = await companionApi.setSystemHome(false);
    return result.changed
      ? 'Restored the stock launcher as default.'
      : 'The stock launcher was already the default.';
  }),
);

// --- BLE provisioning (Web Bluetooth) ---------------------------------------

const bleStatusEl = byId<HTMLParagraphElement>('ble-status');

if (!isWebBluetoothSupported()) {
  setStatus(bleStatusEl, 'Web Bluetooth is not available in this browser.', true);
}

byId<HTMLFormElement>('ble-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const code = byId<HTMLInputElement>('ble-code').value.trim();
  const ssid = byId<HTMLInputElement>('ble-ssid').value.trim();
  const passphraseInput = byId<HTMLInputElement>('ble-passphrase');
  const passphrase = passphraseInput.value;
  const hidden = byId<HTMLInputElement>('ble-hidden').checked;

  // provisionOverBluetooth() is called synchronously from this user-gesture
  // handler (no `await` precedes it) so navigator.bluetooth.requestDevice()
  // sees the click's transient activation.
  void guard(bleStatusEl, async () => {
    const response = await provisionOverBluetooth({ code, ssid, passphrase, hidden });
    // The passphrase is never retained client-side once submitted.
    passphraseInput.value = '';
    if (!response.ok) {
      throw new Error(response.error ?? response.message ?? 'Provisioning failed on the device.');
    }
    if (response.token && response.ipAddress) {
      await companionApi.connectDeviceLan(response.token, response.ipAddress);
      return `Provisioned and connected over LAN at ${response.ipAddress}.`;
    }
    return response.message ?? 'Provisioned, but the device has no Wi-Fi IP address yet.';
  });
});

// --- Device media playback ---------------------------------------------------

const deviceMediaStatusEl = byId<HTMLParagraphElement>('device-media-status');

async function refreshMediaStatus(): Promise<void> {
  const status = await companionApi.mediaStatus();
  setField('mediaState', status.state ?? '—');
  setField('mediaTitle', status.title ?? '—');
  setField(
    'mediaTime',
    status.durationSeconds == null
      ? `${Math.round(status.positionSeconds ?? 0)}s`
      : `${Math.round(status.positionSeconds ?? 0)}s / ${Math.round(status.durationSeconds)}s`,
  );
}

byId<HTMLButtonElement>('refresh-media-status').addEventListener('click', () =>
  guard(deviceMediaStatusEl, async () => {
    await refreshMediaStatus();
    return 'Playback status refreshed.';
  }),
);

byId<HTMLButtonElement>('media-pause').addEventListener('click', () =>
  guard(deviceMediaStatusEl, async () => {
    await companionApi.pauseMedia();
    return 'Paused.';
  }),
);

byId<HTMLButtonElement>('media-resume').addEventListener('click', () =>
  guard(deviceMediaStatusEl, async () => {
    await companionApi.resumeMedia();
    return 'Resumed.';
  }),
);

byId<HTMLButtonElement>('media-stop').addEventListener('click', () =>
  guard(deviceMediaStatusEl, async () => {
    await companionApi.stopMedia();
    return 'Stopped.';
  }),
);

byId<HTMLFormElement>('media-seek-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const time = Number.parseFloat(byId<HTMLInputElement>('media-seek-time').value);
  void guard(deviceMediaStatusEl, async () => {
    await companionApi.seekMedia(Number.isFinite(time) ? time : 0);
    return `Seeked to ${time}s.`;
  });
});

const mediaVolumeInput = byId<HTMLInputElement>('media-volume-value');
const mediaVolumeOutput = byId<HTMLOutputElement>('media-volume-output');
mediaVolumeInput.addEventListener('input', () => {
  mediaVolumeOutput.textContent = mediaVolumeInput.value;
});

byId<HTMLFormElement>('media-volume-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const value = Number.parseFloat(mediaVolumeInput.value);
  void guard(deviceMediaStatusEl, async () => {
    await companionApi.setMediaVolume(value);
    return `Volume set to ${value}.`;
  });
});

// --- Initial load -------------------------------------------------------------

void refreshMedia().catch(() => undefined);
void refreshStatus().catch(() => undefined);
void refreshSystemStatus().catch(() => undefined);
void refreshMediaStatus().catch(() => undefined);
