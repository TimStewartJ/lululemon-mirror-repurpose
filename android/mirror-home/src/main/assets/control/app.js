(function () {
  'use strict';

  var TOKEN_KEY = 'mirror-home-token';
  var CLIENT_ID_KEY = 'mirror-home-client-id';
  var handoff = window.location.hash.indexOf('#handoff=') === 0
    ? window.location.hash.substring('#handoff='.length).split('&')
    : [];
  if (handoff.length) {
    window.localStorage.setItem(TOKEN_KEY, decodeURIComponent(handoff[0]));
    handoff.slice(1).forEach(function (part) {
      if (part.indexOf('clientId=') === 0) {
        window.localStorage.setItem(CLIENT_ID_KEY, decodeURIComponent(part.substring(9)));
      }
    });
    window.history.replaceState(null, document.title, window.location.pathname);
  }
  var token = window.localStorage.getItem(TOKEN_KEY);

  function byId(id) {
    return document.getElementById(id);
  }

  function setMessage(id, message, error) {
    var element = byId(id);
    element.textContent = message || '';
    element.classList.toggle('error', Boolean(error));
  }

  function setConnection(online) {
    var pill = byId('connection-pill');
    pill.textContent = online ? 'Online' : 'Offline';
    pill.className = 'pill ' + (online ? 'online' : 'offline');
  }

  function request(path, options, authenticated) {
    var init = options || {};
    init.headers = Object.assign({ Accept: 'application/json' }, init.headers || {});
    if (init.body && !init.headers['Content-Type']) {
      init.headers['Content-Type'] = 'application/json';
    }
    if (authenticated !== false && token) {
      init.headers.Authorization = 'Bearer ' + token;
    }
    return fetch(path, init).then(function (response) {
      return response.text().then(function (text) {
        var body = text ? JSON.parse(text) : null;
        if (!response.ok) {
          var error = new Error(body && body.error ? body.error : 'Request failed (' + response.status + ')');
          error.status = response.status;
          if (response.status === 401 && authenticated !== false) {
            forgetLocalCredential();
          }
          throw error;
        }
        return body;
      });
    });
  }

  function json(method, body) {
    return { method: method, body: JSON.stringify(body || {}) };
  }

  function showPairedState(isPaired) {
    byId('pairing-card').classList.toggle('hidden', isPaired);
    byId('app-shell').classList.toggle('hidden', !isPaired);
  }

  function formatDate(epoch) {
    if (!epoch) return 'Never';
    return new Date(epoch).toLocaleString();
  }

  function refreshStatus() {
    var authenticated = Boolean(token);
    return request(
      authenticated ? '/api/v1/status' : '/api/v1/bootstrap',
      {},
      !authenticated ? false : true
    ).then(function (status) {
      setConnection(true);
      byId('mirror-name').textContent = status.displayName || 'Mirror Home';
      if (!authenticated) {
        showPairedState(false);
        return status;
      }
      byId('display-name').value = status.displayName || 'Mirror';
      byId('wifi-summary').textContent = status.wifi && status.wifi.connected
        ? status.wifi.ssid || 'Connected'
        : 'Not connected';
      byId('ip-address').textContent = status.wifi && status.wifi.ipAddress
        ? status.wifi.ipAddress + ':8787'
        : 'USB or Bluetooth setup';
      byId('media-summary').textContent = status.media && status.media.state
        ? status.media.state
        : 'idle';
      byId('ble-summary').textContent = status.bleProvisioning || 'Unavailable';
      byId('binder-status').textContent = status.mirrorBinderConnected ? 'Connected' : 'Reconnecting';
      byId('helper-status').textContent = status.systemHelperConnected ? 'Temporary helper active' : 'Factory service';
      byId('app-version').textContent = status.appVersion || 'Unknown';
      if (typeof status.brightness === 'number') {
        byId('brightness').value = String(status.brightness);
        byId('brightness-output').textContent = String(status.brightness);
        byId('brightness-status').textContent = String(status.brightness) + ' / 255';
      }
      showPairedState(Boolean(token));
      return status;
    }).catch(function (error) {
      setConnection(false);
      if (error.status === 401) {
        token = null;
        window.localStorage.removeItem(TOKEN_KEY);
        showPairedState(false);
      }
      throw error;
    });
  }

  function refreshDashboard() {
    if (!token) return Promise.resolve();
    return request('/api/v1/dashboard').then(function (dashboard) {
      var url = dashboard.url || '';
      var localAurora = window.location.origin + '/dashboard/aurora.html';
      var localGallery = window.location.origin + '/dashboard/gallery.html';
      var mode = !url
        ? 'native'
        : (url === localAurora || url === 'http://127.0.0.1:8787/dashboard/aurora.html'
          ? 'aurora'
          : (url === localGallery || url === 'http://127.0.0.1:8787/dashboard/gallery.html'
            ? 'gallery'
            : 'custom'));
      byId('dashboard-mode').value = mode;
      byId('dashboard-url').value = mode === 'custom' ? url : '';
      updateDashboardFields();
    });
  }

  function refreshClients() {
    if (!token) return Promise.resolve();
    return request('/api/v1/clients').then(function (result) {
      var list = byId('client-list');
      list.textContent = '';
      var ownId = window.localStorage.getItem(CLIENT_ID_KEY);
      (result.clients || []).forEach(function (client) {
        var item = document.createElement('li');
        var label = document.createElement('div');
        var strong = document.createElement('strong');
        strong.textContent = client.name + (client.id === ownId ? ' (this browser)' : '');
        var detail = document.createElement('small');
        detail.textContent = 'Last used ' + formatDate(client.lastUsedAt);
        label.appendChild(strong);
        label.appendChild(detail);
        var revoke = document.createElement('button');
        revoke.className = 'secondary';
        revoke.textContent = 'Revoke';
        revoke.addEventListener('click', function () {
          request('/api/v1/clients/revoke', json('POST', { id: client.id }))
            .then(function () {
              if (client.id === ownId) forgetLocalCredential();
              return refreshClients();
            })
            .catch(function (error) { setMessage('access-message', error.message, true); });
        });
        item.appendChild(label);
        item.appendChild(revoke);
        list.appendChild(item);
      });
    });
  }

  function refreshPreferences() {
    if (!token) return Promise.resolve();
    return request('/api/v1/preferences').then(function (preferences) {
      byId('time-zone').value = preferences.timeZone
        || (Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC');
      byId('clock-24-hour').checked = Boolean(preferences.clock24Hour);
      var browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
      var browserOffset = -new Date().getTimezoneOffset();
      if (preferences.timeZone === browserZone
          && preferences.utcOffsetMinutes !== browserOffset) {
        return request('/api/v1/preferences', json('PUT', {
          timeZone: browserZone,
          utcOffsetMinutes: browserOffset,
          clock24Hour: Boolean(preferences.clock24Hour)
        }));
      }
    });
  }

  function refreshAutomation() {
    if (!token) return Promise.resolve();
    return request('/api/v1/automation').then(function (automation) {
      byId('automation-enabled').checked = Boolean(automation.enabled);
      byId('wake-time').value = automation.wakeTime || '07:00';
      byId('sleep-time').value = automation.sleepTime || '23:00';
      byId('wake-brightness').value = String(automation.wakeBrightness || 180);
      byId('ambient-enabled').checked = Boolean(automation.ambientEnabled);
      byId('ambient-enabled').disabled = !automation.ambientLightAvailable;
      byId('automation-capability').textContent = automation.ambientLightAvailable
        ? 'Ambient light sensor available.'
        : 'No ambient-light sensor was detected; time-based brightness remains available.';
    });
  }

  function refreshPhotos() {
    if (!token) return Promise.resolve();
    return request('/api/v1/photos').then(function (result) {
      var list = byId('photo-list');
      list.textContent = '';
      (result.photos || []).forEach(function (photo) {
        var item = document.createElement('li');
        var label = document.createElement('span');
        label.textContent = photo.name;
        var remove = document.createElement('button');
        remove.className = 'secondary';
        remove.textContent = 'Delete';
        remove.addEventListener('click', function () {
          request('/api/v1/photos/' + encodeURIComponent(photo.name), { method: 'DELETE' })
            .then(refreshPhotos)
            .catch(function (error) { setMessage('photo-message', error.message, true); });
        });
        item.appendChild(label);
        item.appendChild(remove);
        list.appendChild(item);
      });
    });
  }

  function refreshOnboarding() {
    if (!token) return Promise.resolve();
    return request('/api/v1/onboarding').then(function (onboarding) {
      byId('onboarding-message').textContent = onboarding.active
        ? 'Recovery setup network is ' + onboarding.state + '.'
        : 'Recovery setup network is off.';
    });
  }

  function refreshAll() {
    return refreshStatus()
      .then(function () {
        return Promise.all([
          refreshDashboard(),
          refreshClients(),
          refreshPreferences(),
          refreshAutomation(),
          refreshOnboarding(),
          refreshPhotos()
        ]);
      })
      .catch(function (error) {
        if (error.status !== 401) setMessage('pair-message', error.message, true);
      });
  }

  function forgetLocalCredential() {
    token = null;
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(CLIENT_ID_KEY);
    showPairedState(false);
  }

  function updateDashboardFields() {
    byId('dashboard-url-row').classList.toggle('hidden', byId('dashboard-mode').value !== 'custom');
  }

  document.querySelectorAll('.tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      document.querySelectorAll('.tab').forEach(function (item) { item.classList.remove('active'); });
      document.querySelectorAll('.panel').forEach(function (item) { item.classList.remove('active'); });
      tab.classList.add('active');
      byId(tab.getAttribute('data-panel')).classList.add('active');
    });
  });

  byId('pair-form').addEventListener('submit', function (event) {
    event.preventDefault();
    setMessage('pair-message', 'Pairing…');
    request('/api/v1/pair', json('POST', {
      code: byId('pair-code').value.trim(),
      name: byId('client-name').value.trim(),
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
      utcOffsetMinutes: -new Date().getTimezoneOffset()
    }), false).then(function (result) {
      token = result.token;
      window.localStorage.setItem(TOKEN_KEY, token);
      if (result.clientId) window.localStorage.setItem(CLIENT_ID_KEY, result.clientId);
      byId('pair-code').value = '';
      setMessage('pair-message', '');
      showPairedState(true);
      return refreshAll();
    }).catch(function (error) {
      setMessage('pair-message', error.message, true);
    });
  });

  byId('refresh-status').addEventListener('click', function () {
    refreshStatus().catch(function (error) { setMessage('pair-message', error.message, true); });
  });

  byId('dashboard-mode').addEventListener('change', updateDashboardFields);
  byId('dashboard-form').addEventListener('submit', function (event) {
    event.preventDefault();
    var mode = byId('dashboard-mode').value;
    var url = mode === 'native'
      ? ''
      : (mode === 'aurora'
        ? 'http://127.0.0.1:8787/dashboard/aurora.html'
        : (mode === 'gallery'
          ? 'http://127.0.0.1:8787/dashboard/gallery.html'
          : byId('dashboard-url').value.trim()));
    request('/api/v1/dashboard', json('PUT', { url: url }))
      .then(function () { setMessage('dashboard-message', 'Dashboard applied.'); })
      .catch(function (error) { setMessage('dashboard-message', error.message, true); });
  });

  byId('name-form').addEventListener('submit', function (event) {
    event.preventDefault();
    request('/api/v1/control/name', json('POST', { name: byId('display-name').value.trim() }))
      .then(function () {
        setMessage('display-message', 'Name saved.');
        return refreshStatus();
      })
      .catch(function (error) { setMessage('display-message', error.message, true); });
  });

  byId('photo-form').addEventListener('submit', function (event) {
    event.preventDefault();
    var file = byId('photo-file').files[0];
    if (!file) return;
    setMessage('photo-message', 'Uploading…');
    fetch('/api/v1/photos/' + encodeURIComponent(file.name), {
      method: 'PUT',
      headers: {
        Authorization: 'Bearer ' + token,
        'Content-Type': file.type || 'image/jpeg'
      },
      body: file
    }).then(function (response) {
      return response.text().then(function (text) {
        var body = text ? JSON.parse(text) : {};
        if (!response.ok) throw new Error(body.error || 'Upload failed (' + response.status + ')');
        return body;
      });
    }).then(function () {
      byId('photo-file').value = '';
      setMessage('photo-message', 'Photo stored locally.');
      return refreshPhotos();
    }).catch(function (error) { setMessage('photo-message', error.message, true); });
  });

  byId('brightness').addEventListener('input', function () {
    byId('brightness-output').textContent = byId('brightness').value;
  });
  byId('brightness-form').addEventListener('submit', function (event) {
    event.preventDefault();
    request('/api/v1/control/brightness', json('POST', { value: Number(byId('brightness').value) }))
      .then(function () {
        setMessage('display-message', 'Brightness updated.');
        return refreshStatus();
      })
      .catch(function (error) { setMessage('display-message', error.message, true); });
  });

  byId('clock-form').addEventListener('submit', function (event) {
    event.preventDefault();
    request('/api/v1/preferences', json('PUT', {
      timeZone: byId('time-zone').value.trim(),
      utcOffsetMinutes: -new Date().getTimezoneOffset(),
      clock24Hour: byId('clock-24-hour').checked
    })).then(function () {
      setMessage('clock-message', 'Clock settings saved.');
      return refreshStatus();
    }).catch(function (error) { setMessage('clock-message', error.message, true); });
  });

  byId('automation-form').addEventListener('submit', function (event) {
    event.preventDefault();
    request('/api/v1/automation', json('PUT', {
      enabled: byId('automation-enabled').checked,
      wakeTime: byId('wake-time').value,
      sleepTime: byId('sleep-time').value,
      wakeBrightness: Number(byId('wake-brightness').value),
      ambientEnabled: byId('ambient-enabled').checked,
      ambientMinimum: 20,
      ambientMaximum: 220
    })).then(function () {
      setMessage('automation-message', 'Schedule saved.');
      return refreshAutomation();
    }).catch(function (error) { setMessage('automation-message', error.message, true); });
  });

  byId('wake-now').addEventListener('click', function () {
    request('/api/v1/automation/wake', json('POST', {}))
      .then(function () { setMessage('automation-message', 'Mirror awake for the next four hours.'); })
      .catch(function (error) { setMessage('automation-message', error.message, true); });
  });

  byId('sleep-now').addEventListener('click', function () {
    request('/api/v1/automation/sleep', json('POST', {}))
      .then(function () { setMessage('automation-message', 'Mirror sleeping. Use Wake now to restore it.'); })
      .catch(function (error) { setMessage('automation-message', error.message, true); });
  });

  byId('wifi-form').addEventListener('submit', function (event) {
    event.preventDefault();
    var passphrase = byId('wifi-passphrase').value;
    setMessage('wifi-message', 'Connecting…');
    request('/api/v1/wifi/configure', json('POST', {
      ssid: byId('wifi-ssid').value.trim(),
      passphrase: passphrase,
      hidden: byId('wifi-hidden').checked
    })).then(function (result) {
      byId('wifi-passphrase').value = '';
      setMessage('wifi-message', result.message || 'Connection requested.');
      if (result.ipAddress && result.ipAddress !== window.location.hostname) {
        var clientId = window.localStorage.getItem(CLIENT_ID_KEY) || '';
        window.location.href = 'http://' + result.ipAddress + ':' + (result.apiPort || 8787)
          + '/#handoff=' + encodeURIComponent(token)
          + '&clientId=' + encodeURIComponent(clientId);
        return;
      }
      window.setTimeout(refreshStatus, 4000);
    }).catch(function (error) {
      byId('wifi-passphrase').value = '';
      setMessage('wifi-message', error.message, true);
    });

    byId('start-setup-network').addEventListener('click', function () {
      request('/api/v1/onboarding/start', json('POST', {}))
        .then(function () {
          setMessage('onboarding-message', 'Starting the recovery setup network…');
          window.setTimeout(refreshOnboarding, 3000);
        })
        .catch(function (error) { setMessage('onboarding-message', error.message, true); });
    });

    byId('stop-setup-network').addEventListener('click', function () {
      request('/api/v1/onboarding/stop', json('POST', {}))
        .then(function () { return refreshOnboarding(); })
        .catch(function (error) { setMessage('onboarding-message', error.message, true); });
    });
  });

  byId('media-form').addEventListener('submit', function (event) {
    event.preventDefault();
    request('/api/v1/media/play', json('POST', {
      url: byId('media-url').value.trim(),
      title: byId('media-title').value.trim(),
      volume: byId('media-muted').checked ? 0 : 1
    })).then(function () {
      setMessage('media-message', byId('media-muted').checked ? 'Playing silently.' : 'Playing.');
      return refreshStatus();
    }).catch(function (error) { setMessage('media-message', error.message, true); });
  });

  [['media-pause', 'pause'], ['media-resume', 'resume'], ['media-stop', 'stop']].forEach(function (entry) {
    byId(entry[0]).addEventListener('click', function () {
      request('/api/v1/media/' + entry[1], json('POST', {}))
        .then(function () {
          setMessage('media-message', entry[1] === 'stop' ? 'Playback stopped.' : 'Playback ' + entry[1] + 'd.');
          return refreshStatus();
        })
        .catch(function (error) { setMessage('media-message', error.message, true); });
    });
  });

  byId('refresh-clients').addEventListener('click', function () {
    refreshClients().catch(function (error) { setMessage('access-message', error.message, true); });
  });

  byId('forget-this-device').addEventListener('click', function () {
    request('/api/v1/pair/revoke', json('POST', {}))
      .catch(function () { return null; })
      .then(forgetLocalCredential);
  });

  updateDashboardFields();
  if (!byId('time-zone').value) {
    byId('time-zone').value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  }
  showPairedState(Boolean(token));
  refreshAll();
  window.setInterval(function () {
    if (token) refreshStatus().catch(function () {});
  }, 15000);
}());
