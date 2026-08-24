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
  var dashboardLayout = null;
  var weatherSnapshot = null;
  var selectedWidgetId = 'clock';
  var layoutPhotoObjectUrl = null;
  var layoutHistory = [];
  var layoutFuture = [];
  var layoutBaseline = '';
  var layoutGestureActive = false;
  var weatherPollTimer = null;
  var layoutSettings = {
    snap: window.localStorage.getItem('mirror-layout-snap') !== 'false',
    grid: Number(window.localStorage.getItem('mirror-layout-grid') || 20),
    safeZone: window.localStorage.getItem('mirror-layout-safe-zone') !== 'false'
  };
  var widgetLabels = {
    clock: 'Clock',
    date: 'Date',
    name: 'Mirror name',
    wifi: 'Wi-Fi',
    media: 'Media',
    schedule: 'Sleep schedule',
    brightness: 'Brightness',
    fcast: 'FCast',
    ble: 'Bluetooth',
    uptime: 'Uptime',
    motion: 'Motion presence',
    weather: 'Current weather',
    forecast: 'Hourly forecast',
    pairing: 'Pairing code',
    note: 'Custom note'
  };

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

  function cloneValue(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function layoutText() {
    return dashboardLayout ? JSON.stringify(dashboardLayout) : '';
  }

  function resetLayoutHistory() {
    layoutHistory = [];
    layoutFuture = [];
    layoutBaseline = layoutText();
    updateHistoryButtons();
  }

  function commitLayoutChange() {
    var current = layoutText();
    if (!current || current === layoutBaseline) return;
    layoutHistory.push(layoutBaseline);
    if (layoutHistory.length > 80) layoutHistory.shift();
    layoutFuture = [];
    layoutBaseline = current;
    updateHistoryButtons();
  }

  function updateHistoryButtons() {
    byId('layout-undo').disabled = layoutHistory.length === 0;
    byId('layout-redo').disabled = layoutFuture.length === 0;
  }

  function applyLayoutSnapshot(serialized) {
    dashboardLayout = JSON.parse(serialized);
    layoutBaseline = serialized;
    if (!selectedWidget()) {
      selectedWidgetId = dashboardLayout.widgets.length
        ? dashboardLayout.widgets[0].id
        : '';
    }
    rebuildWidgetSelector();
    renderLayoutEditor();
    updateHistoryButtons();
  }

  function gridSize() {
    var value = Number(layoutSettings.grid);
    return [5, 10, 20, 25, 50].indexOf(value) >= 0 ? value : 20;
  }

  function snapValue(value) {
    return layoutSettings.snap
      ? Math.round(value / gridSize()) * gridSize()
      : Math.round(value);
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function sortedWidgets() {
    if (!dashboardLayout) return [];
    return dashboardLayout.widgets.slice().sort(function (left, right) {
      var layer = Number(left.layer || 0) - Number(right.layer || 0);
      return layer || left.id.localeCompare(right.id);
    });
  }

  function widgetLabel(widget) {
    var base = widgetLabels[widget.type] || widget.type;
    return widget.id === widget.type ? base : base + ' (' + widget.id + ')';
  }

  function uniqueWidgetId(type) {
    var used = {};
    dashboardLayout.widgets.forEach(function (widget) { used[widget.id] = true; });
    for (var number = 2; number < 1000; number++) {
      var candidate = (type + '-' + number).substring(0, 40);
      if (!used[candidate]) return candidate;
    }
    return type + '-copy';
  }

  function formatTemperature(value) {
    return typeof value === 'number' ? Math.round(value) + '\u00b0' : '--';
  }

  function weatherPreviewText(type) {
    var data = weatherSnapshot && weatherSnapshot.data;
    if (!data || !data.current) {
      return type === 'forecast'
        ? '9 AM  64\u00b0   12 PM  69\u00b0'
        : '68\u00b0  PARTLY CLOUDY';
    }
    if (type === 'weather') {
      var today = data.daily && data.daily[0];
      return formatTemperature(data.current.temperature) + '  ' +
        String(data.current.condition || '').toUpperCase() +
        (today ? '  ' + formatTemperature(today.high) + ' / ' +
          formatTemperature(today.low) : '');
    }
    return (data.hourly || []).slice(0, 3).map(function (hour) {
      return new Date(hour.time).toLocaleTimeString([], { hour: 'numeric' }) +
        '  ' + formatTemperature(hour.temperature);
    }).join('   ') || 'Forecast waiting';
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
      if (status.weather) {
        weatherSnapshot = status.weather;
        renderWeatherStatus(status.weather);
        if (dashboardLayout
            && !layoutGestureActive
            && byId('display').classList.contains('active')) {
          renderLayoutEditor();
        }
      }
      if (status.automation) renderMotionStatus(status.automation);
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

  function selectedWidget() {
    if (!dashboardLayout) return null;
    return dashboardLayout.widgets.find(function (widget) {
      return widget.id === selectedWidgetId;
    }) || null;
  }

  function previewWidgetText(widget) {
    var examples = {
      clock: '8:42',
      date: 'SATURDAY, AUGUST 23',
      name: byId('display-name').value || 'Mirror',
      wifi: 'WI-FI  HOME',
      media: 'MEDIA  IDLE',
      schedule: 'RHYTHM  SLEEP 10:30',
      brightness: 'LIGHT  190',
      fcast: 'FCAST  READY',
      ble: 'BLUETOOTH  READY',
      uptime: 'UPTIME  3H 18M',
      motion: 'PRESENCE  WATCHING',
      weather: weatherPreviewText('weather'),
      forecast: weatherPreviewText('forecast'),
      pairing: 'PAIR  123456',
      note: widget.text || 'Make space for what matters.'
    };
    return examples[widget.type] || widget.type;
  }

  function updateLayoutPreviewBackground() {
    if (!dashboardLayout) return;
    var preview = byId('layout-preview');
    var background = dashboardLayout.background;
    var photoLayer = preview.querySelector('.layout-preview-photo');
    var shadeLayer = preview.querySelector('.layout-preview-shade');
    if (!photoLayer) {
      photoLayer = document.createElement('div');
      photoLayer.className = 'layout-preview-photo';
      shadeLayer = document.createElement('div');
      shadeLayer.className = 'layout-preview-shade';
      preview.append(photoLayer, shadeLayer);
    }
    preview.style.background = background.mode === 'solid'
      ? background.primary
      : 'linear-gradient(155deg,' + background.primary + ',' + background.secondary + ')';
    shadeLayer.style.background = 'rgba(0,0,0,' + (Number(background.dim) / 100) + ')';
    photoLayer.style.backgroundImage = '';
    if (layoutPhotoObjectUrl) {
      URL.revokeObjectURL(layoutPhotoObjectUrl);
      layoutPhotoObjectUrl = null;
    }
    if (background.mode === 'photo' && background.photo) {
      fetch('/api/v1/photos/' + encodeURIComponent(background.photo), {
        headers: { Authorization: 'Bearer ' + token }
      }).then(function (response) {
        if (!response.ok) throw new Error('Background photo unavailable');
        return response.blob();
      }).then(function (blob) {
        layoutPhotoObjectUrl = URL.createObjectURL(blob);
        photoLayer.style.backgroundImage = 'url("' + layoutPhotoObjectUrl + '")';
      }).catch(function () {
        photoLayer.style.backgroundImage = '';
      });
    }
  }

  function updateWidgetControls() {
    var widget = selectedWidget();
    if (!widget) return;
    byId('layout-widget-select').value = widget.id;
    byId('layout-widget-visible').checked = Boolean(widget.visible);
    byId('layout-widget-align').value = widget.align;
    byId('layout-widget-opacity').value = String(widget.opacity);
    byId('layout-opacity-output').textContent = widget.opacity + '%';
    byId('layout-widget-x').value = String(widget.x);
    byId('layout-widget-y').value = String(widget.y);
    byId('layout-widget-width').value = String(widget.w);
    byId('layout-widget-height').value = String(widget.h);
    byId('layout-widget-locked').checked = Boolean(widget.locked);
    byId('layout-widget-layer').value = String(widget.layer || 0);
    byId('layout-note-row').classList.toggle('hidden', widget.type !== 'note');
    byId('layout-note-text').value = widget.text || '';
    [
      'layout-widget-x',
      'layout-widget-y',
      'layout-widget-width',
      'layout-widget-height'
    ].forEach(function (id) {
      byId(id).disabled = Boolean(widget.locked);
    });
    byId('layout-delete-widget').disabled =
      dashboardLayout.widgets.length <= 1 || widget.id === widget.type;
  }

  function renderLayoutEditor() {
    if (!dashboardLayout) return;
    var preview = byId('layout-preview');
    Array.from(preview.querySelectorAll('.layout-widget')).forEach(function (element) {
      element.remove();
    });
    Array.from(preview.querySelectorAll('.layout-guide')).forEach(function (element) {
      element.remove();
    });
    updateLayoutPreviewBackground();
    var safeZone = preview.querySelector('.layout-safe-zone');
    if (!safeZone) {
      safeZone = document.createElement('div');
      safeZone.className = 'layout-safe-zone';
      preview.appendChild(safeZone);
    }
    safeZone.classList.toggle('hidden', !layoutSettings.safeZone);
    sortedWidgets().forEach(function (widget) {
      var element = document.createElement('div');
      element.className = 'layout-widget layout-widget-' + widget.type +
        ' align-' + widget.align +
        (widget.id === selectedWidgetId ? ' selected' : '') +
        (!widget.visible ? ' is-hidden' : '') +
        (widget.locked ? ' is-locked' : '');
      element.dataset.widgetId = widget.id;
      element.tabIndex = 0;
      element.setAttribute('role', 'button');
      element.setAttribute('aria-label', widgetLabel(widget) + ' widget');
      element.style.left = (widget.x / 10) + '%';
      element.style.top = (widget.y / 10) + '%';
      element.style.width = (widget.w / 10) + '%';
      element.style.height = (widget.h / 10) + '%';
      element.style.opacity = String(widget.opacity / 100);
      element.style.zIndex = String(2 + Number(widget.layer || 0));
      element.style.color = ['clock', 'date', 'name', 'note'].indexOf(widget.type) >= 0
        ? dashboardLayout.textColor
        : dashboardLayout.accentColor;
      element.textContent = previewWidgetText(widget);
      var handle = document.createElement('span');
      handle.className = 'layout-resize-handle';
      handle.setAttribute('aria-hidden', 'true');
      element.appendChild(handle);
      preview.appendChild(element);
    });
    byId('layout-background-mode').value = dashboardLayout.background.mode;
    byId('layout-primary-color').value = dashboardLayout.background.primary;
    byId('layout-secondary-color').value = dashboardLayout.background.secondary;
    byId('layout-background-dim').value = String(dashboardLayout.background.dim);
    byId('layout-dim-output').textContent = dashboardLayout.background.dim + '%';
    byId('layout-text-color').value = dashboardLayout.textColor;
    byId('layout-accent-color').value = dashboardLayout.accentColor;
    byId('layout-background-photo').value = dashboardLayout.background.photo || '';
    byId('layout-background-photo-row').classList.toggle(
      'hidden',
      dashboardLayout.background.mode !== 'photo'
    );
    updateWidgetControls();
    byId('layout-snap-enabled').checked = layoutSettings.snap;
    byId('layout-grid-size').value = String(gridSize());
    byId('layout-safe-zone').checked = layoutSettings.safeZone;
  }

  function rebuildWidgetSelector() {
    if (!dashboardLayout) return;
    var selector = byId('layout-widget-select');
    selector.textContent = '';
    sortedWidgets().forEach(function (widget) {
      var option = document.createElement('option');
      option.value = widget.id;
      option.textContent = widgetLabel(widget);
      selector.appendChild(option);
    });
    selector.value = selectedWidgetId;
  }

  function refreshDashboardLayout() {
    if (!token) return Promise.resolve();
    return request('/api/v1/dashboard/layout').then(function (layout) {
      dashboardLayout = layout;
      if (!selectedWidget()) selectedWidgetId = dashboardLayout.widgets[0].id;
      rebuildWidgetSelector();
      resetLayoutHistory();
      renderLayoutEditor();
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
      byId('motion-enabled').checked = Boolean(automation.motionEnabled);
      byId('motion-timeout-minutes').value =
        String(Number(automation.motionTimeoutSeconds || 300) / 60);
      byId('motion-sensitivity').value = String(automation.motionSensitivity || 6);
      byId('motion-sensitivity-output').textContent =
        String(automation.motionSensitivity || 6);
      byId('automation-capability').textContent = automation.ambientLightAvailable
        ? 'Ambient light sensor available.'
        : 'No ambient-light sensor was detected; time-based brightness remains available.';
      renderMotionStatus(automation);
    });
  }

  function renderMotionStatus(automation) {
    var element = byId('motion-status');
    var motion = automation.motion || {};
    var text = 'Motion sensing is off.';
    var isError = false;
    if (automation.motionEnabled) {
      if (!motion.available) {
        text = 'No compatible camera was detected. The mirror will remain awake inside its schedule.';
        isError = true;
      } else if (!motion.permissionGranted) {
        text = 'Camera permission is waiting for approval on the Mirror. Until then, inactivity will not put the display to sleep.';
      } else if (motion.monitoring) {
        text = 'Monitoring locally';
        if (motion.previewWidth && motion.previewHeight) {
          text += ' at ' + motion.previewWidth + 'x' + motion.previewHeight;
        }
        text += '.';
        if (typeof motion.lastMotionAgeSeconds === 'number') {
          text += motion.lastMotionAgeSeconds < 5
            ? ' Movement seen just now.'
            : ' Last movement ' + motion.lastMotionAgeSeconds + ' seconds ago.';
        } else {
          text += ' Waiting for movement.';
        }
        if (typeof motion.score === 'number') {
          text += ' Change score ' + motion.score + '%.';
        }
        if (automation.sleeping && automation.sleepReason === 'inactivity') {
          text += ' Backlight is off until movement is seen.';
        }
      } else if (motion.state === 'starting') {
        text = 'Starting the local camera monitor.';
      } else {
        text = motion.error || 'Camera monitoring is paused. The mirror will remain awake inside its schedule.';
        isError = motion.state === 'error';
      }
    }
    element.textContent = text;
    element.classList.toggle('error', isError);
  }

  function renderWeatherStatus(weather) {
    var text = 'Weather is not configured.';
    var isError = false;
    if (weather && weather.config && weather.config.enabled) {
      var data = weather.data;
      if (data && data.current) {
        var today = data.daily && data.daily[0];
        text = formatTemperature(data.current.temperature) + ' ' +
          data.current.condition;
        if (today) {
          text += ' · High ' + formatTemperature(today.high) +
            ' · Low ' + formatTemperature(today.low) +
            ' · Rain ' + today.precipitationProbability + '%';
        }
        if (weather.stale) text += ' · Cached forecast is stale';
        if (weather.updatedAt) text += ' · Updated ' + formatDate(weather.updatedAt);
      } else if (weather.refreshing || weather.state === 'waiting') {
        text = 'Waiting for the first forecast.';
      } else {
        text = weather.error || 'Weather data is unavailable.';
        isError = true;
      }
    }
    setMessage('weather-message', text, isError);
  }

  function refreshWeather() {
    if (!token) return Promise.resolve();
    return request('/api/v1/weather').then(function (weather) {
      weatherSnapshot = weather;
      var config = weather.config || {};
      byId('weather-enabled').checked = Boolean(config.enabled);
      byId('weather-location-name').value = config.locationName || '';
      byId('weather-latitude').value =
        typeof config.latitude === 'number' ? String(config.latitude) : '';
      byId('weather-longitude').value =
        typeof config.longitude === 'number' ? String(config.longitude) : '';
      byId('weather-units').value = config.units || 'us';
      renderWeatherStatus(weather);
      if (!layoutGestureActive) renderLayoutEditor();
      return weather;
    });
  }

  function pollWeatherUntilSettled(remainingAttempts) {
    if (weatherPollTimer) {
      window.clearTimeout(weatherPollTimer);
      weatherPollTimer = null;
    }
    return refreshWeather().then(function (weather) {
      var waiting = weather.refreshing
        || (!weather.data && weather.state !== 'error' && weather.state !== 'unconfigured');
      if (waiting && remainingAttempts > 0) {
        weatherPollTimer = window.setTimeout(function () {
          pollWeatherUntilSettled(remainingAttempts - 1).catch(function (error) {
            setMessage('weather-message', error.message, true);
          });
        }, 2000);
      }
      return weather;
    });
  }

  function refreshPhotos() {
    if (!token) return Promise.resolve();
    return request('/api/v1/photos').then(function (result) {
      var list = byId('photo-list');
      var backgroundSelect = byId('layout-background-photo');
      var selectedBackground = dashboardLayout ? dashboardLayout.background.photo : '';
      list.textContent = '';
      backgroundSelect.textContent = '';
      var noPhoto = document.createElement('option');
      noPhoto.value = '';
      noPhoto.textContent = 'Select a gallery photo';
      backgroundSelect.appendChild(noPhoto);
      (result.photos || []).forEach(function (photo) {
        var option = document.createElement('option');
        option.value = photo.name;
        option.textContent = photo.name;
        backgroundSelect.appendChild(option);
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
      backgroundSelect.value = selectedBackground || '';
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
          refreshDashboardLayout(),
          refreshClients(),
          refreshPreferences(),
          refreshAutomation(),
          refreshWeather(),
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

  function updateLayoutBackground(event) {
    if (!dashboardLayout) return;
    dashboardLayout.background.mode = byId('layout-background-mode').value;
    dashboardLayout.background.primary = byId('layout-primary-color').value;
    dashboardLayout.background.secondary = byId('layout-secondary-color').value;
    dashboardLayout.background.photo = byId('layout-background-photo').value;
    dashboardLayout.background.dim = Number(byId('layout-background-dim').value);
    dashboardLayout.textColor = byId('layout-text-color').value;
    dashboardLayout.accentColor = byId('layout-accent-color').value;
    byId('layout-dim-output').textContent = dashboardLayout.background.dim + '%';
    renderLayoutEditor();
    if (event && event.type === 'change') commitLayoutChange();
  }

  [
    'layout-background-mode',
    'layout-primary-color',
    'layout-secondary-color',
    'layout-background-photo',
    'layout-background-dim',
    'layout-text-color',
    'layout-accent-color'
  ].forEach(function (id) {
    byId(id).addEventListener('input', updateLayoutBackground);
    byId(id).addEventListener('change', updateLayoutBackground);
  });

  byId('layout-widget-select').addEventListener('change', function () {
    selectedWidgetId = byId('layout-widget-select').value;
    renderLayoutEditor();
  });

  byId('layout-widget-visible').addEventListener('change', function () {
    var widget = selectedWidget();
    if (!widget) return;
    widget.visible = byId('layout-widget-visible').checked;
    renderLayoutEditor();
    commitLayoutChange();
  });

  byId('layout-widget-locked').addEventListener('change', function () {
    var widget = selectedWidget();
    if (!widget) return;
    widget.locked = byId('layout-widget-locked').checked;
    renderLayoutEditor();
    commitLayoutChange();
  });

  byId('layout-widget-align').addEventListener('change', function () {
    var widget = selectedWidget();
    if (!widget) return;
    widget.align = byId('layout-widget-align').value;
    renderLayoutEditor();
    commitLayoutChange();
  });

  byId('layout-widget-opacity').addEventListener('input', function () {
    var widget = selectedWidget();
    if (!widget) return;
    widget.opacity = Number(byId('layout-widget-opacity').value);
    byId('layout-opacity-output').textContent = widget.opacity + '%';
    renderLayoutEditor();
  });
  byId('layout-widget-opacity').addEventListener('change', commitLayoutChange);

  byId('layout-note-text').addEventListener('input', function () {
    var widget = selectedWidget();
    if (!widget || widget.type !== 'note') return;
    widget.text = byId('layout-note-text').value;
    renderLayoutEditor();
  });
  byId('layout-note-text').addEventListener('change', commitLayoutChange);

  function updateWidgetGeometry() {
    var widget = selectedWidget();
    if (!widget) return;
    var x = Number(byId('layout-widget-x').value);
    var y = Number(byId('layout-widget-y').value);
    var width = Number(byId('layout-widget-width').value);
    var height = Number(byId('layout-widget-height').value);
    width = Math.max(24, Math.min(1000 - x, width));
    height = Math.max(24, Math.min(1000 - y, height));
    widget.x = Math.max(0, Math.min(1000 - width, x));
    widget.y = Math.max(0, Math.min(1000 - height, y));
    widget.w = width;
    widget.h = height;
    renderLayoutEditor();
    commitLayoutChange();
  }

  [
    'layout-widget-x',
    'layout-widget-y',
    'layout-widget-width',
    'layout-widget-height'
  ].forEach(function (id) {
    byId(id).addEventListener('change', updateWidgetGeometry);
  });

  byId('layout-widget-layer').addEventListener('change', function () {
    var widget = selectedWidget();
    if (!widget) return;
    widget.layer = clamp(Number(byId('layout-widget-layer').value), 0, 99);
    renderLayoutEditor();
    rebuildWidgetSelector();
    commitLayoutChange();
  });

  function changeWidgetLayer(delta) {
    var widget = selectedWidget();
    if (!widget) return;
    widget.layer = clamp(Number(widget.layer || 0) + delta, 0, 99);
    rebuildWidgetSelector();
    renderLayoutEditor();
    commitLayoutChange();
  }

  byId('layout-send-backward').addEventListener('click', function () {
    changeWidgetLayer(-1);
  });
  byId('layout-bring-forward').addEventListener('click', function () {
    changeWidgetLayer(1);
  });

  byId('layout-duplicate-widget').addEventListener('click', function () {
    var widget = selectedWidget();
    if (!widget || dashboardLayout.widgets.length >= 40) {
      setMessage('layout-message', 'The dashboard supports at most 40 widgets.', true);
      return;
    }
    var duplicate = cloneValue(widget);
    duplicate.id = uniqueWidgetId(widget.type);
    duplicate.locked = false;
    duplicate.layer = clamp(Number(widget.layer || 0) + 1, 0, 99);
    duplicate.x = clamp(snapValue(widget.x + gridSize()), 0, 1000 - widget.w);
    duplicate.y = clamp(snapValue(widget.y + gridSize()), 0, 1000 - widget.h);
    dashboardLayout.widgets.push(duplicate);
    selectedWidgetId = duplicate.id;
    rebuildWidgetSelector();
    renderLayoutEditor();
    commitLayoutChange();
    setMessage('layout-message', widgetLabel(duplicate) + ' created.');
  });

  byId('layout-delete-widget').addEventListener('click', function () {
    var widget = selectedWidget();
    if (!widget || widget.id === widget.type || dashboardLayout.widgets.length <= 1) return;
    var index = dashboardLayout.widgets.indexOf(widget);
    dashboardLayout.widgets.splice(index, 1);
    selectedWidgetId = dashboardLayout.widgets[Math.max(0, index - 1)].id;
    rebuildWidgetSelector();
    renderLayoutEditor();
    commitLayoutChange();
    setMessage('layout-message', 'Duplicate widget deleted.');
  });

  byId('layout-undo').addEventListener('click', function () {
    if (!layoutHistory.length) return;
    layoutFuture.push(layoutText());
    applyLayoutSnapshot(layoutHistory.pop());
    setMessage('layout-message', 'Last editor change undone.');
  });

  byId('layout-redo').addEventListener('click', function () {
    if (!layoutFuture.length) return;
    layoutHistory.push(layoutText());
    applyLayoutSnapshot(layoutFuture.pop());
    setMessage('layout-message', 'Editor change restored.');
  });

  byId('layout-snap-enabled').addEventListener('change', function () {
    layoutSettings.snap = byId('layout-snap-enabled').checked;
    window.localStorage.setItem('mirror-layout-snap', String(layoutSettings.snap));
  });
  byId('layout-grid-size').addEventListener('change', function () {
    layoutSettings.grid = Number(byId('layout-grid-size').value);
    window.localStorage.setItem('mirror-layout-grid', String(gridSize()));
  });
  byId('layout-safe-zone').addEventListener('change', function () {
    layoutSettings.safeZone = byId('layout-safe-zone').checked;
    window.localStorage.setItem(
      'mirror-layout-safe-zone',
      String(layoutSettings.safeZone)
    );
    renderLayoutEditor();
  });

  byId('layout-export').addEventListener('click', function () {
    if (!dashboardLayout) return;
    var blob = new Blob(
      [JSON.stringify(dashboardLayout, null, 2) + '\n'],
      { type: 'application/json' }
    );
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = 'mirror-dashboard-layout.json';
    link.click();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  });

  byId('layout-import').addEventListener('click', function () {
    byId('layout-import-file').click();
  });
  byId('layout-import-file').addEventListener('change', function () {
    var file = byId('layout-import-file').files[0];
    if (!file) return;
    if (file.size > 60 * 1024) {
      setMessage('layout-message', 'Layout file exceeds 60 KB.', true);
      byId('layout-import-file').value = '';
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      try {
        var imported = JSON.parse(String(reader.result || ''));
        request(
          '/api/v1/dashboard/layout/validate',
          json('POST', imported)
        ).then(function (normalized) {
          layoutHistory.push(layoutText());
          layoutFuture = [];
          dashboardLayout = normalized;
          selectedWidgetId = normalized.widgets[0].id;
          layoutBaseline = layoutText();
          rebuildWidgetSelector();
          renderLayoutEditor();
          updateHistoryButtons();
          setMessage('layout-message', 'Layout imported. Save to apply it.');
        }).catch(function (error) {
          setMessage('layout-message', error.message || 'Invalid layout file.', true);
        });
      } catch (error) {
        setMessage('layout-message', 'Invalid layout JSON.', true);
      }
      byId('layout-import-file').value = '';
    };
    reader.onerror = function () {
      setMessage('layout-message', 'Unable to read the layout file.', true);
      byId('layout-import-file').value = '';
    };
    reader.readAsText(file);
  });

  byId('save-dashboard-layout').addEventListener('click', function () {
    if (!dashboardLayout) return;
    setMessage('layout-message', 'Saving…');
    request('/api/v1/dashboard/layout', json('PUT', dashboardLayout))
      .then(function (saved) {
        dashboardLayout = saved;
        rebuildWidgetSelector();
        resetLayoutHistory();
        return request('/api/v1/dashboard', json('PUT', { url: '' }));
      })
      .then(function () {
        byId('dashboard-mode').value = 'native';
        updateDashboardFields();
        renderLayoutEditor();
        setMessage('layout-message', 'Built-in dashboard updated.');
      })
      .catch(function (error) { setMessage('layout-message', error.message, true); });
  });

  byId('reset-dashboard-layout').addEventListener('click', function () {
    setMessage('layout-message', 'Restoring the subtle default…');
    request('/api/v1/dashboard/layout/reset', json('POST', {}))
      .then(function (layout) {
        dashboardLayout = layout;
        selectedWidgetId = 'clock';
        rebuildWidgetSelector();
        resetLayoutHistory();
        return request('/api/v1/dashboard', json('PUT', { url: '' }));
      })
      .then(function () {
        byId('dashboard-mode').value = 'native';
        updateDashboardFields();
        renderLayoutEditor();
        setMessage('layout-message', 'Subtle default restored.');
      })
      .catch(function (error) { setMessage('layout-message', error.message, true); });
  });

  (function enableLayoutGestures() {
    var preview = byId('layout-preview');
    var gesture = null;

    function removeGuides() {
      Array.from(preview.querySelectorAll('.layout-guide')).forEach(function (guide) {
        guide.remove();
      });
    }

    function showGuide(axis, value) {
      var guide = document.createElement('div');
      guide.className = 'layout-guide ' + axis;
      if (axis === 'vertical') guide.style.left = (value / 10) + '%';
      else guide.style.top = (value / 10) + '%';
      preview.appendChild(guide);
    }

    function nearestAlignment(anchors, targets) {
      var best = null;
      anchors.forEach(function (anchor) {
        targets.forEach(function (target) {
          var delta = target - anchor;
          if (Math.abs(delta) <= 8 && (!best || Math.abs(delta) < Math.abs(best.delta))) {
            best = { delta: delta, target: target };
          }
        });
      });
      return best;
    }

    function alignmentTargets(widget) {
      var vertical = [0, 500, 1000];
      var horizontal = [0, 500, 1000];
      dashboardLayout.widgets.forEach(function (other) {
        if (other.id === widget.id || !other.visible) return;
        vertical.push(other.x, other.x + other.w / 2, other.x + other.w);
        horizontal.push(other.y, other.y + other.h / 2, other.y + other.h);
      });
      return { vertical: vertical, horizontal: horizontal };
    }

    function alignGeometry(widget, geometry, resize) {
      removeGuides();
      var targets = alignmentTargets(widget);
      var vertical = nearestAlignment(
        resize
          ? [geometry.x + geometry.w]
          : [geometry.x, geometry.x + geometry.w / 2, geometry.x + geometry.w],
        targets.vertical
      );
      var horizontal = nearestAlignment(
        resize
          ? [geometry.y + geometry.h]
          : [geometry.y, geometry.y + geometry.h / 2, geometry.y + geometry.h],
        targets.horizontal
      );
      if (vertical) {
        if (resize) geometry.w += vertical.delta;
        else geometry.x += vertical.delta;
        showGuide('vertical', vertical.target);
      }
      if (horizontal) {
        if (resize) geometry.h += horizontal.delta;
        else geometry.y += horizontal.delta;
        showGuide('horizontal', horizontal.target);
      }
      geometry.w = clamp(geometry.w, 24, 1000 - geometry.x);
      geometry.h = clamp(geometry.h, 24, 1000 - geometry.y);
      geometry.x = clamp(geometry.x, 0, 1000 - geometry.w);
      geometry.y = clamp(geometry.y, 0, 1000 - geometry.h);
      return geometry;
    }

    preview.addEventListener('pointerdown', function (event) {
      var element = event.target.closest('.layout-widget');
      if (!element || !dashboardLayout) return;
      selectedWidgetId = element.dataset.widgetId;
      Array.from(preview.querySelectorAll('.layout-widget')).forEach(function (item) {
        item.classList.toggle('selected', item === element);
      });
      updateWidgetControls();
      var widget = selectedWidget();
      if (!widget || widget.locked) {
        renderLayoutEditor();
        return;
      }
      var bounds = preview.getBoundingClientRect();
      layoutGestureActive = true;
      gesture = {
        pointerId: event.pointerId,
        element: element,
        resize: event.target.classList.contains('layout-resize-handle'),
        startX: event.clientX,
        startY: event.clientY,
        canvasWidth: bounds.width,
        canvasHeight: bounds.height,
        x: widget.x,
        y: widget.y,
        w: widget.w,
        h: widget.h
      };
      preview.setPointerCapture(event.pointerId);
      event.preventDefault();
    });

    preview.addEventListener('pointermove', function (event) {
      if (!gesture || event.pointerId !== gesture.pointerId) return;
      var widget = selectedWidget();
      if (!widget) return;
      var deltaX = Math.round((event.clientX - gesture.startX) / gesture.canvasWidth * 1000);
      var deltaY = Math.round((event.clientY - gesture.startY) / gesture.canvasHeight * 1000);
      var geometry = {
        x: gesture.x,
        y: gesture.y,
        w: gesture.w,
        h: gesture.h
      };
      if (gesture.resize) {
        geometry.w = clamp(snapValue(gesture.w + deltaX), 24, 1000 - widget.x);
        geometry.h = clamp(snapValue(gesture.h + deltaY), 24, 1000 - widget.y);
      } else {
        geometry.x = clamp(snapValue(gesture.x + deltaX), 0, 1000 - widget.w);
        geometry.y = clamp(snapValue(gesture.y + deltaY), 0, 1000 - widget.h);
      }
      geometry = alignGeometry(widget, geometry, gesture.resize);
      widget.x = Math.round(geometry.x);
      widget.y = Math.round(geometry.y);
      widget.w = Math.round(geometry.w);
      widget.h = Math.round(geometry.h);
      gesture.element.style.left = (widget.x / 10) + '%';
      gesture.element.style.top = (widget.y / 10) + '%';
      gesture.element.style.width = (widget.w / 10) + '%';
      gesture.element.style.height = (widget.h / 10) + '%';
      event.preventDefault();
    });

    function endGesture(event) {
      if (!gesture || event.pointerId !== gesture.pointerId) return;
      if (preview.hasPointerCapture(event.pointerId)) {
        preview.releasePointerCapture(event.pointerId);
      }
      gesture = null;
      layoutGestureActive = false;
      removeGuides();
      renderLayoutEditor();
      commitLayoutChange();
    }

    preview.addEventListener('pointerup', endGesture);
    preview.addEventListener('pointercancel', endGesture);

    preview.addEventListener('keydown', function (event) {
      var element = event.target.closest('.layout-widget');
      if (!element || ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown']
        .indexOf(event.key) < 0) return;
      selectedWidgetId = element.dataset.widgetId;
      var widget = selectedWidget();
      if (!widget || widget.locked) return;
      var step = gridSize();
      var horizontal = event.key === 'ArrowLeft' ? -step
        : (event.key === 'ArrowRight' ? step : 0);
      var vertical = event.key === 'ArrowUp' ? -step
        : (event.key === 'ArrowDown' ? step : 0);
      if (event.shiftKey) {
        widget.w = clamp(snapValue(widget.w + horizontal), 24, 1000 - widget.x);
        widget.h = clamp(snapValue(widget.h + vertical), 24, 1000 - widget.y);
      } else {
        widget.x = clamp(snapValue(widget.x + horizontal), 0, 1000 - widget.w);
        widget.y = clamp(snapValue(widget.y + vertical), 0, 1000 - widget.h);
      }
      renderLayoutEditor();
      commitLayoutChange();
      event.preventDefault();
    });
  }());

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

  function enableWeatherWidgetIfNeeded() {
    if (!dashboardLayout || !byId('weather-enabled').checked) {
      return Promise.resolve();
    }
    var weatherWidget = dashboardLayout.widgets.find(function (widget) {
      return widget.type === 'weather';
    });
    if (!weatherWidget || weatherWidget.visible) {
      return Promise.resolve();
    }
    var overlapsVisibleWidget = dashboardLayout.widgets.some(function (other) {
      if (other.id === weatherWidget.id || !other.visible) return false;
      return weatherWidget.x < other.x + other.w
        && weatherWidget.x + weatherWidget.w > other.x
        && weatherWidget.y < other.y + other.h
        && weatherWidget.y + weatherWidget.h > other.y;
    });
    if (overlapsVisibleWidget) {
      weatherWidget.x = 605;
      weatherWidget.y = 120;
      weatherWidget.w = 340;
      weatherWidget.h = 145;
    }
    weatherWidget.visible = true;
    commitLayoutChange();
    renderLayoutEditor();
    return request('/api/v1/dashboard/layout', json('PUT', dashboardLayout))
      .then(function (normalized) {
        dashboardLayout = normalized;
        rebuildWidgetSelector();
        resetLayoutHistory();
        renderLayoutEditor();
      });
  }

  byId('weather-form').addEventListener('submit', function (event) {
    event.preventDefault();
    var enabled = byId('weather-enabled').checked;
    var body = {
      enabled: enabled,
      locationName: byId('weather-location-name').value.trim(),
      units: byId('weather-units').value
    };
    if (enabled) {
      var latitudeValue = byId('weather-latitude').value.trim();
      var longitudeValue = byId('weather-longitude').value.trim();
      if (!latitudeValue || !longitudeValue) {
        setMessage(
          'weather-message',
          'Choose a search result or enter both latitude and longitude.',
          true
        );
        return;
      }
      body.latitude = Number(latitudeValue);
      body.longitude = Number(longitudeValue);
    }
    setMessage('weather-message', 'Saving weather settings…');
    request('/api/v1/weather', json('PUT', body))
      .then(function (weather) {
        weatherSnapshot = weather;
        return enableWeatherWidgetIfNeeded();
      })
      .then(function () {
        setMessage('weather-message', 'Weather saved. Fetching the forecast…');
        return request('/api/v1/weather/refresh', json('POST', {}));
      })
      .then(function () {
        weatherPollTimer = window.setTimeout(function () {
          pollWeatherUntilSettled(12).catch(function (error) {
            setMessage('weather-message', error.message, true);
          });
        }, 1000);
      })
      .catch(function (error) {
        setMessage('weather-message', error.message, true);
      });
  });

  byId('weather-use-location').addEventListener('click', function () {
    if (!window.isSecureContext || !navigator.geolocation) {
      setMessage(
        'weather-message',
        'Browser location requires HTTPS or localhost. Use city search instead.',
        true
      );
      return;
    }
    setMessage('weather-message', 'Requesting this browser’s location…');
    navigator.geolocation.getCurrentPosition(function (position) {
      byId('weather-latitude').value = position.coords.latitude.toFixed(5);
      byId('weather-longitude').value = position.coords.longitude.toFixed(5);
      if (!byId('weather-location-name').value.trim()) {
        byId('weather-location-name').value = 'Home';
      }
      byId('weather-enabled').checked = true;
      setMessage('weather-message', 'Location filled in. Save weather to apply it.');
    }, function (error) {
      setMessage(
        'weather-message',
        error.message || 'Unable to read this browser’s location.',
        true
      );
    }, {
      enableHighAccuracy: false,
      timeout: 15000,
      maximumAge: 10 * 60 * 1000
    });
  });

  byId('weather-search').addEventListener('click', function () {
    var query = byId('weather-place-search').value.trim();
    if (query.length < 2) {
    setMessage('weather-message', 'Enter at least two characters to search.', true);
    return;
    }
    setMessage('weather-message', 'Searching locations…');
    request('/api/v1/weather/locations?q=' + encodeURIComponent(query))
    .then(function (result) {
      var selector = byId('weather-search-results');
      selector.textContent = '';
      (result.results || []).forEach(function (location, index) {
        var option = document.createElement('option');
        option.value = String(index);
        option.textContent = location.label;
        option.dataset.latitude = String(location.latitude);
        option.dataset.longitude = String(location.longitude);
        option.dataset.label = location.label;
        selector.appendChild(option);
      });
      byId('weather-search-results-row').classList.toggle(
        'hidden',
        !selector.options.length
      );
      if (!selector.options.length) {
        setMessage('weather-message', 'No matching locations were found.', true);
        return;
      }
      selector.dispatchEvent(new Event('change'));
      setMessage('weather-message', 'Location selected. Save weather to apply it.');
    })
    .catch(function (error) {
      setMessage('weather-message', error.message, true);
    });
  });

  byId('weather-search-results').addEventListener('change', function () {
    var option = byId('weather-search-results').selectedOptions[0];
    if (!option) return;
    byId('weather-latitude').value = Number(option.dataset.latitude).toFixed(5);
    byId('weather-longitude').value = Number(option.dataset.longitude).toFixed(5);
    byId('weather-location-name').value = option.dataset.label;
    byId('weather-enabled').checked = true;
  });

  if (!window.isSecureContext || !navigator.geolocation) {
    byId('weather-use-location').disabled = true;
    byId('weather-use-location').title =
    'Browser location requires HTTPS or localhost; use city search instead.';
  }

  byId('weather-refresh').addEventListener('click', function () {
    setMessage('weather-message', 'Refreshing the forecast…');
    request('/api/v1/weather/refresh', json('POST', {}))
      .then(function () {
        weatherPollTimer = window.setTimeout(function () {
          pollWeatherUntilSettled(12).catch(function (error) {
            setMessage('weather-message', error.message, true);
          });
        }, 1000);
      })
      .catch(function (error) {
        setMessage('weather-message', error.message, true);
      });
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
      ambientMaximum: 220,
      motionEnabled: byId('motion-enabled').checked,
      motionTimeoutSeconds: Math.round(Number(byId('motion-timeout-minutes').value) * 60),
      motionSensitivity: Number(byId('motion-sensitivity').value)
    })).then(function () {
      setMessage('automation-message', 'Automation saved.');
      return refreshAutomation();
    }).catch(function (error) { setMessage('automation-message', error.message, true); });
  });

  byId('motion-sensitivity').addEventListener('input', function () {
    byId('motion-sensitivity-output').textContent = byId('motion-sensitivity').value;
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
