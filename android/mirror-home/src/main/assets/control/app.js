(function () {
  'use strict';

  var TOKEN_KEY = 'mirror-home-token';
  var CLIENT_ID_KEY = 'mirror-home-client-id';
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
    return request('/api/v1/status', {}, false).then(function (status) {
      setConnection(true);
      byId('mirror-name').textContent = status.displayName || 'Mirror Home';
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
      var mode = !url ? 'native' : (url === localAurora || url === 'http://127.0.0.1:8787/dashboard/aurora.html' ? 'aurora' : 'custom');
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

  function refreshAll() {
    return refreshStatus()
      .then(function () { return Promise.all([refreshDashboard(), refreshClients()]); })
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
      name: byId('client-name').value.trim()
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
        : byId('dashboard-url').value.trim());
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
      window.setTimeout(refreshStatus, 4000);
    }).catch(function (error) {
      byId('wifi-passphrase').value = '';
      setMessage('wifi-message', error.message, true);
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
  showPairedState(Boolean(token));
  refreshAll();
  window.setInterval(function () {
    if (token) refreshStatus().catch(function () {});
  }, 15000);
}());
