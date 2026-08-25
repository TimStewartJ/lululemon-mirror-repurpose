(function () {
  'use strict';

  var TOKEN_KEY = 'mirror-home-token';
  var CLIENT_ID_KEY = 'mirror-home-client-id';
  var LOCAL_AURORA = 'http://127.0.0.1:8787/dashboard/aurora.html';
  var LOCAL_GALLERY = 'http://127.0.0.1:8787/dashboard/gallery.html';

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
  var status = null;
  var savedLayout = null;
  var dashboardLayout = null;
  var weatherSnapshot = null;
  var photoList = [];
  var selectedWidgetId = 'clock';
  var layoutHistory = [];
  var layoutFuture = [];
  var layoutBaseline = '';
  var layoutGestureActive = false;
  var weatherPollTimer = null;
  var toastTimer = null;
  var photoUrlCache = {};
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
    schedule: 'Schedule',
    brightness: 'Brightness',
    fcast: 'Casting',
    ble: 'Bluetooth',
    uptime: 'Uptime',
    motion: 'Presence',
    weather: 'Weather',
    forecast: 'Hourly forecast',
    pairing: 'Pairing code',
    note: 'Note'
  };

  function byId(id) { return document.getElementById(id); }

  function setMessage(id, message, error) {
    var element = byId(id);
    if (!element) return;
    element.textContent = message || '';
    element.classList.toggle('error', Boolean(error));
  }

  function toast(message, error) {
    var element = byId('toast');
    element.textContent = message;
    element.className = 'toast show' + (error ? ' error' : '');
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      element.className = 'toast' + (error ? ' error' : '');
    }, error ? 4200 : 2400);
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
          if (response.status === 401 && authenticated !== false) forgetLocalCredential();
          throw error;
        }
        return body;
      });
    });
  }

  function json(method, body) {
    return { method: method, body: JSON.stringify(body || {}) };
  }

  function cloneValue(value) { return JSON.parse(JSON.stringify(value)); }

  function formatDate(epoch) {
    if (!epoch) return 'never';
    var date = new Date(epoch);
    var now = new Date();
    var sameDay = date.toDateString() === now.toDateString();
    var time = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    if (sameDay) return 'today at ' + time;
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' at ' + time;
  }

  function formatTemperature(value) {
    return typeof value === 'number' ? Math.round(value) + '\u00b0' : '--';
  }

  function formatUptime(seconds) {
    var total = Math.max(0, Number(seconds || 0));
    var days = Math.floor(total / 86400);
    var hours = Math.floor((total % 86400) / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    if (days) return days + 'd ' + hours + 'h';
    if (hours) return hours + 'h ' + minutes + 'm';
    return minutes + 'm';
  }

  function formatClockTime(value) {
    if (!value) return '';
    var pieces = String(value).split(':');
    var hours = Number(pieces[0]);
    var minutes = pieces[1] || '00';
    if (status && status.clock24Hour) return (hours < 10 ? '0' : '') + hours + ':' + minutes;
    return (hours % 12 || 12) + ':' + minutes + ' ' + (hours >= 12 ? 'PM' : 'AM');
  }

  /* ---------- Shared mirror renderers ---------- */

  function resolvePhoto(name, done) {
    if (photoUrlCache[name]) { done(photoUrlCache[name]); return; }
    fetch('/api/v1/photos/' + encodeURIComponent(name), {
      headers: { Authorization: 'Bearer ' + token }
    }).then(function (response) {
      if (!response.ok) throw new Error('Photo unavailable');
      return response.blob();
    }).then(function (blob) {
      photoUrlCache[name] = URL.createObjectURL(blob);
      done(photoUrlCache[name]);
    }).catch(function () { done(''); });
  }

  var homeRenderer = window.MirrorRenderer.create(byId('home-preview'), { resolvePhoto: resolvePhoto });
  var editor = window.MirrorRenderer.create(byId('layout-preview'), { editing: true, resolvePhoto: resolvePhoto });

  function previewRuntime() {
    return status || {};
  }

  function renderHomePreview() {
    var note = byId('home-preview-note');
    var url = status && status.dashboardUrl;
    if (url) {
      note.classList.remove('hidden');
      note.textContent = url === LOCAL_AURORA ? 'Aurora is showing on the mirror'
        : (url === LOCAL_GALLERY ? 'Photos are showing on the mirror' : 'A web page is showing on the mirror');
      return;
    }
    note.classList.add('hidden');
    if (savedLayout) homeRenderer.update(savedLayout, previewRuntime());
  }

  function alignedTick() {
    var now = new Date();
    homeRenderer.tick(now);
    editor.tick(now);
    window.setTimeout(alignedTick, 1000 - (Date.now() % 1000) + 10);
  }

  /* ---------- Layout editor state ---------- */

  function layoutText() { return dashboardLayout ? JSON.stringify(dashboardLayout) : ''; }

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
      selectedWidgetId = dashboardLayout.widgets.length ? dashboardLayout.widgets[0].id : '';
    }
    renderLayoutEditor();
    updateHistoryButtons();
  }

  function gridSize() {
    var value = Number(layoutSettings.grid);
    return [5, 10, 20, 25, 50].indexOf(value) >= 0 ? value : 20;
  }

  function snapValue(value) {
    return layoutSettings.snap ? Math.round(value / gridSize()) * gridSize() : Math.round(value);
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
    return widget.id === widget.type ? base : base + ' ' + widget.id.replace(widget.type + '-', '');
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

  function selectedWidget() {
    if (!dashboardLayout) return null;
    return dashboardLayout.widgets.find(function (widget) { return widget.id === selectedWidgetId; }) || null;
  }

  function setRadio(name, value) {
    var inputs = document.querySelectorAll('input[name="' + name + '"]');
    Array.from(inputs).forEach(function (input) { input.checked = input.value === value; });
  }

  function radioValue(name) {
    var checked = document.querySelector('input[name="' + name + '"]:checked');
    return checked ? checked.value : '';
  }

  function renderLayers() {
    var list = byId('layout-layers');
    list.textContent = '';
    sortedWidgets().forEach(function (widget) {
      var item = document.createElement('li');
      item.className = 'layer' + (widget.id === selectedWidgetId ? ' selected' : '') + (widget.visible ? '' : ' is-hidden');
      item.setAttribute('role', 'button');
      item.tabIndex = 0;
      var name = document.createElement('span');
      name.textContent = widgetLabel(widget);
      item.appendChild(name);
      if (widget.locked) {
        var lock = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        lock.setAttribute('class', 'lock');
        lock.innerHTML = '<use href="#i-lock"/>';
        item.appendChild(lock);
      }
      var eye = document.createElement('button');
      eye.type = 'button';
      eye.className = 'eye';
      eye.setAttribute('aria-label', (widget.visible ? 'Hide ' : 'Show ') + widgetLabel(widget));
      eye.innerHTML = '<svg><use href="#' + (widget.visible ? 'i-eye' : 'i-eye-off') + '"/></svg>';
      eye.addEventListener('click', function (event) {
        event.stopPropagation();
        widget.visible = !widget.visible;
        selectedWidgetId = widget.id;
        renderLayoutEditor();
        commitLayoutChange();
      });
      item.appendChild(eye);
      item.addEventListener('click', function () {
        selectedWidgetId = widget.id;
        renderLayoutEditor();
      });
      item.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          selectedWidgetId = widget.id;
          renderLayoutEditor();
        }
      });
      list.appendChild(item);
    });
  }

  function updateWidgetControls() {
    var widget = selectedWidget();
    if (!widget) return;
    byId('layout-widget-title').textContent = widgetLabel(widget);
    byId('layout-widget-visible').checked = Boolean(widget.visible);
    byId('layout-widget-locked').checked = Boolean(widget.locked);
    setRadio('widget-align', widget.align);
    byId('layout-widget-opacity').value = String(widget.opacity);
    byId('layout-opacity-output').textContent = widget.opacity + '%';
    byId('layout-widget-x').value = String(widget.x);
    byId('layout-widget-y').value = String(widget.y);
    byId('layout-widget-width').value = String(widget.w);
    byId('layout-widget-height').value = String(widget.h);
    byId('layout-widget-layer').value = String(widget.layer || 0);
    byId('layout-note-row').classList.toggle('hidden', widget.type !== 'note');
    byId('layout-note-text').value = widget.text || '';
    ['layout-widget-x', 'layout-widget-y', 'layout-widget-width', 'layout-widget-height'].forEach(function (id) {
      byId(id).disabled = Boolean(widget.locked);
    });
    byId('layout-delete-widget').disabled = dashboardLayout.widgets.length <= 1 || widget.id === widget.type;
  }

  function ensureCanvasChrome() {
    var canvas = byId('layout-preview');
    if (!canvas.querySelector('.mr-grid')) {
      var grid = document.createElement('div');
      grid.className = 'mr-grid';
      canvas.insertBefore(grid, editor.layer);
    }
    var safe = canvas.querySelector('.mr-safe');
    if (!safe) {
      safe = document.createElement('div');
      safe.className = 'mr-safe';
      canvas.insertBefore(safe, editor.layer);
    }
    safe.classList.toggle('hidden', !layoutSettings.safeZone);
  }

  function renderLayoutEditor() {
    if (!dashboardLayout) return;
    editor.update(dashboardLayout, previewRuntime());
    editor.setSelected(selectedWidgetId);
    ensureCanvasChrome();
    setRadio('bg-mode', dashboardLayout.background.mode);
    byId('layout-primary-color').value = dashboardLayout.background.primary;
    byId('layout-secondary-color').value = dashboardLayout.background.secondary;
    byId('layout-background-dim').value = String(dashboardLayout.background.dim);
    byId('layout-dim-output').textContent = dashboardLayout.background.dim + '%';
    byId('layout-text-color').value = dashboardLayout.textColor;
    byId('layout-accent-color').value = dashboardLayout.accentColor;
    byId('layout-background-photo').value = dashboardLayout.background.photo || '';
    byId('layout-secondary-row').classList.toggle('hidden', dashboardLayout.background.mode !== 'gradient');
    byId('layout-background-photo-row').classList.toggle('hidden', dashboardLayout.background.mode !== 'photo');
    byId('layout-dim-row').classList.toggle('hidden', dashboardLayout.background.mode !== 'photo');
    renderLayers();
    updateWidgetControls();
    byId('layout-snap-enabled').checked = layoutSettings.snap;
    byId('layout-grid-size').value = String(gridSize());
    byId('layout-safe-zone').checked = layoutSettings.safeZone;
  }

  /* ---------- Data refresh ---------- */

  function showPairedState(isPaired) {
    byId('pairing-card').classList.toggle('hidden', isPaired);
    byId('app-shell').classList.toggle('hidden', !isPaired);
  }

  function setLive(state, text) {
    var dot = byId('live-dot');
    dot.className = 'live-dot ' + state;
    byId('live-text').textContent = text;
  }

  function renderHomeStatus() {
    if (!status) return;
    var automation = status.automation || {};
    var wifi = status.wifi || {};
    var pieces = [];
    if (automation.sleeping) {
      pieces.push(automation.sleepReason === 'inactivity' ? 'Asleep until someone is near' : 'Asleep');
      if (automation.enabled && automation.wakeTime && automation.sleepReason !== 'inactivity') {
        pieces[0] = 'Asleep until ' + formatClockTime(automation.wakeTime);
      }
    } else {
      pieces.push('Awake');
      if (automation.enabled && automation.sleepTime) pieces.push('sleeps ' + formatClockTime(automation.sleepTime));
    }
    pieces.push(wifi.connected ? (wifi.ssid || 'Online') : 'No Wi-Fi');
    byId('home-status').textContent = pieces.join(' \u00b7 ');
    byId('wake-now').classList.toggle('active', !automation.sleeping);
    byId('sleep-now').classList.toggle('active', Boolean(automation.sleeping));
    setLive(automation.sleeping ? 'sleeping' : 'online', automation.sleeping ? 'Sleeping' : 'Live');
  }

  function renderNowPlaying() {
    var media = status && status.media ? status.media : {};
    var active = media.state && media.state !== 'idle' && media.state !== 'error';
    byId('now-playing').classList.toggle('hidden', !active);
    if (!active) return;
    byId('np-title').textContent = media.title || media.url || 'Media';
    byId('np-state').textContent = media.state.charAt(0).toUpperCase() + media.state.slice(1) +
      (media.volume === 0 ? ' \u00b7 muted' : '');
    byId('media-pause').classList.toggle('hidden', media.state !== 'playing' && media.state !== 'buffering');
    byId('media-resume').classList.toggle('hidden', media.state !== 'paused');
  }

  function refreshStatus() {
    var authenticated = Boolean(token);
    return request(authenticated ? '/api/v1/status' : '/api/v1/bootstrap', {}, authenticated)
      .then(function (next) {
        byId('mirror-name').textContent = next.displayName || 'Mirror';
        byId('pairing-mirror-name').textContent = next.displayName || 'your Mirror';
        if (!authenticated) {
          showPairedState(false);
          return next;
        }
        var previousUrl = status ? status.dashboardUrl : undefined;
        next.dashboardUrl = previousUrl;
        status = next;
        if (document.activeElement !== byId('display-name')) {
          byId('display-name').value = next.displayName || 'Mirror';
        }
        if (typeof next.brightness === 'number' && next.brightness > 0) {
          if (document.activeElement !== byId('brightness')) {
            byId('brightness').value = String(next.brightness);
          }
          byId('brightness-output').textContent = Math.round(next.brightness / 255 * 100) + '%';
        } else if (next.automation && next.automation.sleeping) {
          byId('brightness-output').textContent = 'Off';
        }
        byId('about-version').textContent = next.appVersion || '—';
        byId('about-address').textContent = next.wifi && next.wifi.ipAddress
          ? next.wifi.ipAddress + ':8787' : 'USB or Bluetooth only';
        byId('about-binder').textContent = next.mirrorBinderConnected ? 'Connected' : 'Reconnecting';
        byId('about-helper').textContent = next.systemHelperConnected ? 'Temporary helper active' : 'Factory service';
        byId('about-ble').textContent = next.bleProvisioning
          ? String(next.bleProvisioning).charAt(0).toUpperCase() + String(next.bleProvisioning).slice(1)
          : 'Unavailable';
        byId('about-uptime').textContent = formatUptime(next.deviceUptimeSeconds);
        if (next.weather) {
          weatherSnapshot = next.weather;
          renderWeatherStatus(next.weather);
        }
        if (next.automation) renderMotionStatus(next.automation);
        renderHomeStatus();
        renderNowPlaying();
        renderHomePreview();
        if (dashboardLayout && !layoutGestureActive) editor.update(dashboardLayout, previewRuntime());
        showPairedState(true);
        return next;
      })
      .catch(function (error) {
        setLive('offline', 'Offline');
        if (error.status === 401) {
          token = null;
          window.localStorage.removeItem(TOKEN_KEY);
          showPairedState(false);
        }
        throw error;
      });
  }

  function sourceFromUrl(url) {
    if (!url) return 'native';
    if (url === LOCAL_AURORA || url === window.location.origin + '/dashboard/aurora.html') return 'aurora';
    if (url === LOCAL_GALLERY || url === window.location.origin + '/dashboard/gallery.html') return 'gallery';
    return 'custom';
  }

  function refreshDashboard() {
    if (!token) return Promise.resolve();
    return request('/api/v1/dashboard').then(function (dashboard) {
      var url = dashboard.url || '';
      if (status) status.dashboardUrl = url;
      var mode = sourceFromUrl(url);
      setRadio('source', mode);
      byId('dashboard-url').value = mode === 'custom' ? url : '';
      byId('dashboard-url-row').classList.toggle('hidden', mode !== 'custom');
      renderHomePreview();
    });
  }

  function refreshDashboardLayout() {
    if (!token) return Promise.resolve();
    return request('/api/v1/dashboard/layout').then(function (layout) {
      savedLayout = cloneValue(layout);
      dashboardLayout = layout;
      if (!selectedWidget()) selectedWidgetId = dashboardLayout.widgets[0].id;
      resetLayoutHistory();
      renderLayoutEditor();
      renderHomePreview();
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
        strong.textContent = client.name;
        if (client.id === ownId) {
          var you = document.createElement('span');
          you.className = 'you';
          you.textContent = 'THIS BROWSER';
          strong.appendChild(you);
        }
        var detail = document.createElement('small');
        detail.textContent = 'Last used ' + formatDate(client.lastUsedAt);
        label.appendChild(strong);
        label.appendChild(detail);
        var revoke = document.createElement('button');
        revoke.type = 'button';
        revoke.className = 'btn small';
        revoke.textContent = 'Revoke';
        revoke.addEventListener('click', function () {
          request('/api/v1/clients/revoke', json('POST', { id: client.id }))
            .then(function () {
              if (client.id === ownId) forgetLocalCredential();
              toast(client.name + ' revoked');
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
      var browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
      byId('time-zone').value = preferences.timeZone || browserZone;
      byId('clock-24-hour').checked = Boolean(preferences.clock24Hour);
      var browserOffset = -new Date().getTimezoneOffset();
      if (preferences.timeZone === browserZone && preferences.utcOffsetMinutes !== browserOffset) {
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
      byId('wake-brightness-output').textContent = Math.round((automation.wakeBrightness || 180) / 255 * 100) + '%';
      byId('ambient-enabled').checked = Boolean(automation.ambientEnabled);
      byId('ambient-enabled').disabled = !automation.ambientLightAvailable;
      byId('ambient-hint').textContent = automation.ambientLightAvailable
        ? 'Dims with the room'
        : 'No ambient-light sensor on this mirror';
      byId('motion-enabled').checked = Boolean(automation.motionEnabled);
      byId('motion-timeout-minutes').value = String(Number(automation.motionTimeoutSeconds || 300) / 60);
      byId('motion-sensitivity').value = String(automation.motionSensitivity || 6);
      byId('motion-sensitivity-output').textContent = String(automation.motionSensitivity || 6);
      renderMotionStatus(automation);
    });
  }

  function renderMotionStatus(automation) {
    var element = byId('motion-status');
    var motion = automation.motion || {};
    var text = 'Presence sensing is off.';
    var isError = false;
    if (automation.motionEnabled) {
      if (!motion.available) {
        text = 'No compatible camera was found. The mirror stays awake inside its schedule.';
        isError = true;
      } else if (!motion.permissionGranted) {
        text = 'Camera permission is waiting for approval on the mirror.';
      } else if (motion.monitoring) {
        text = 'Watching locally';
        if (typeof motion.lastMotionAgeSeconds === 'number') {
          text += motion.lastMotionAgeSeconds < 5
            ? ' \u00b7 movement just now'
            : ' \u00b7 last movement ' + motion.lastMotionAgeSeconds + 's ago';
        } else {
          text += ' \u00b7 no movement yet';
        }
        if (automation.sleeping && automation.sleepReason === 'inactivity') text += ' \u00b7 display off until someone is near';
      } else if (motion.state === 'starting') {
        text = 'Starting the camera…';
      } else {
        text = motion.error || 'Camera paused. The mirror stays awake inside its schedule.';
        isError = motion.state === 'error';
      }
    }
    element.textContent = text;
    element.classList.toggle('error', isError);
  }

  function renderWeatherStatus(weather) {
    var text = '';
    var isError = false;
    if (weather && weather.config && weather.config.enabled) {
      var data = weather.data;
      if (data && data.current) {
        var today = data.daily && data.daily[0];
        text = formatTemperature(data.current.temperature) + ' ' + data.current.condition;
        if (today) text += ' \u00b7 H ' + formatTemperature(today.high) + ' L ' + formatTemperature(today.low);
        if (weather.config.locationName) text += ' \u00b7 ' + weather.config.locationName;
        if (weather.stale) text += ' \u00b7 cached';
      } else if (weather.refreshing || weather.state === 'waiting') {
        text = 'Fetching the first forecast…';
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
      if (config.locationName && !byId('weather-place-search').value) {
        byId('weather-place-search').value = config.locationName;
      }
      byId('weather-latitude').value = typeof config.latitude === 'number' ? String(config.latitude) : '';
      byId('weather-longitude').value = typeof config.longitude === 'number' ? String(config.longitude) : '';
      setRadio('weather-units', config.units || 'us');
      renderWeatherStatus(weather);
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
      } else {
        refreshStatus().catch(function () {});
      }
      return weather;
    });
  }

  function refreshPhotos() {
    if (!token) return Promise.resolve();
    return request('/api/v1/photos').then(function (result) {
      photoList = result.photos || [];
      var grid = byId('photo-grid');
      var add = byId('photo-add');
      Array.from(grid.querySelectorAll('.photo-tile')).forEach(function (tile) { tile.remove(); });
      var backgroundSelect = byId('layout-background-photo');
      var selectedBackground = dashboardLayout ? dashboardLayout.background.photo : '';
      backgroundSelect.textContent = '';
      var noPhoto = document.createElement('option');
      noPhoto.value = '';
      noPhoto.textContent = 'Choose a photo';
      backgroundSelect.appendChild(noPhoto);
      photoList.forEach(function (photo) {
        var option = document.createElement('option');
        option.value = photo.name;
        option.textContent = photo.name;
        backgroundSelect.appendChild(option);

        var tile = document.createElement('figure');
        tile.className = 'photo-tile' + (photo.name === selectedBackground ? ' in-use' : '');
        tile.style.margin = '0';
        var image = document.createElement('img');
        image.alt = photo.name;
        image.loading = 'lazy';
        fetch('/api/v1/photos/' + encodeURIComponent(photo.name) + '/thumbnail', {
          headers: { Authorization: 'Bearer ' + token }
        }).then(function (response) {
          if (!response.ok) throw new Error('thumbnail');
          return response.blob();
        }).then(function (blob) {
          image.onload = function () { image.classList.add('ready'); };
          image.src = URL.createObjectURL(blob);
        }).catch(function () {
          resolvePhoto(photo.name, function (url) {
            if (url) { image.src = url; image.classList.add('ready'); }
          });
        });
        var remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'remove';
        remove.setAttribute('aria-label', 'Delete ' + photo.name);
        remove.innerHTML = '<svg><use href="#i-close"/></svg>';
        remove.addEventListener('click', function () {
          if (!window.confirm('Delete ' + photo.name + ' from the mirror?')) return;
          request('/api/v1/photos/' + encodeURIComponent(photo.name), { method: 'DELETE' })
            .then(function () { toast('Photo deleted'); return refreshPhotos(); })
            .catch(function (error) { setMessage('photo-message', error.message, true); });
        });
        tile.appendChild(image);
        tile.appendChild(remove);
        grid.insertBefore(tile, add);
      });
      backgroundSelect.value = selectedBackground || '';
    });
  }

  function refreshOnboarding() {
    if (!token) return Promise.resolve();
    return request('/api/v1/onboarding').then(function (onboarding) {
      byId('onboarding-status').textContent = onboarding.active
        ? 'Recovery Wi-Fi is ' + onboarding.state
        : 'Off. Start it if the mirror loses Wi-Fi.';
      byId('start-setup-network').classList.toggle('hidden', Boolean(onboarding.active));
      byId('stop-setup-network').classList.toggle('hidden', !onboarding.active);
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

  /* ---------- Navigation ---------- */

  function showPanel(name) {
    document.querySelectorAll('.tab').forEach(function (item) {
      item.classList.toggle('active', item.getAttribute('data-panel') === name);
    });
    document.querySelectorAll('.panel').forEach(function (item) {
      item.classList.toggle('active', item.id === name);
    });
    window.requestAnimationFrame(function () {
      if (name === 'display' && dashboardLayout) editor.relayout();
      if (name === 'home' && savedLayout) homeRenderer.relayout();
    });
  }

  document.querySelectorAll('.tab').forEach(function (tab) {
    tab.addEventListener('click', function () { showPanel(tab.getAttribute('data-panel')); });
  });

  byId('home-preview-button').addEventListener('click', function () {
    showPanel('display');
    var editorElement = document.querySelector('.editor');
    if (editorElement) editorElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  /* ---------- Pairing ---------- */

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

  byId('pair-code').addEventListener('input', function () {
    var field = byId('pair-code');
    field.value = field.value.replace(/\D/g, '').slice(0, 6);
  });

  /* ---------- Home ---------- */

  byId('brightness').addEventListener('input', function () {
    byId('brightness-output').textContent = Math.round(Number(byId('brightness').value) / 255 * 100) + '%';
  });
  byId('brightness').addEventListener('change', function () {
    request('/api/v1/control/brightness', json('POST', { value: Number(byId('brightness').value) }))
      .then(function () { return refreshStatus(); })
      .catch(function (error) { setMessage('home-message', error.message, true); });
  });

  byId('wake-now').addEventListener('click', function () {
    request('/api/v1/automation/wake', json('POST', {}))
      .then(function () { toast('Mirror awake for the next four hours'); return refreshStatus(); })
      .catch(function (error) { setMessage('home-message', error.message, true); });
  });

  byId('sleep-now').addEventListener('click', function () {
    request('/api/v1/automation/sleep', json('POST', {}))
      .then(function () { toast('Mirror sleeping'); return refreshStatus(); })
      .catch(function (error) { setMessage('home-message', error.message, true); });
  });

  byId('media-form').addEventListener('submit', function (event) {
    event.preventDefault();
    request('/api/v1/media/play', json('POST', {
      url: byId('media-url').value.trim(),
      title: byId('media-title').value.trim(),
      volume: byId('media-muted').checked ? 0 : 1
    })).then(function () {
      setMessage('media-message', '');
      toast(byId('media-muted').checked ? 'Playing silently on the mirror' : 'Playing on the mirror');
      return refreshStatus();
    }).catch(function (error) { setMessage('media-message', error.message, true); });
  });

  [['media-pause', 'pause'], ['media-resume', 'resume'], ['media-stop', 'stop']].forEach(function (entry) {
    byId(entry[0]).addEventListener('click', function () {
      request('/api/v1/media/' + entry[1], json('POST', {}))
        .then(function () { return refreshStatus(); })
        .catch(function (error) { setMessage('home-message', error.message, true); });
    });
  });

  /* ---------- Display: source ---------- */

  function applyDashboardUrl(url, successMessage) {
    return request('/api/v1/dashboard', json('PUT', { url: url }))
      .then(function () {
        if (status) status.dashboardUrl = url;
        setMessage('dashboard-message', '');
        if (successMessage) toast(successMessage);
        renderHomePreview();
      })
      .catch(function (error) { setMessage('dashboard-message', error.message, true); });
  }

  Array.from(document.querySelectorAll('input[name="source"]')).forEach(function (input) {
    input.addEventListener('change', function () {
      var mode = input.value;
      byId('dashboard-url-row').classList.toggle('hidden', mode !== 'custom');
      if (mode === 'custom') {
        byId('dashboard-url').focus();
        return;
      }
      applyDashboardUrl(
        mode === 'native' ? '' : (mode === 'aurora' ? LOCAL_AURORA : LOCAL_GALLERY),
        mode === 'native' ? 'Mirror dashboard is showing' : (mode === 'aurora' ? 'Aurora is showing' : 'Photos are showing')
      );
    });
  });

  byId('dashboard-form').addEventListener('submit', function (event) {
    event.preventDefault();
    applyDashboardUrl(byId('dashboard-url').value.trim(), 'Web page is showing on the mirror');
  });

  /* ---------- Display: editor controls ---------- */

  function updateLayoutBackground(event) {
    if (!dashboardLayout) return;
    dashboardLayout.background.mode = radioValue('bg-mode') || dashboardLayout.background.mode;
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

  ['layout-primary-color', 'layout-secondary-color', 'layout-background-photo', 'layout-background-dim',
    'layout-text-color', 'layout-accent-color'].forEach(function (id) {
    byId(id).addEventListener('input', updateLayoutBackground);
    byId(id).addEventListener('change', updateLayoutBackground);
  });
  Array.from(document.querySelectorAll('input[name="bg-mode"]')).forEach(function (input) {
    input.addEventListener('change', updateLayoutBackground);
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

  Array.from(document.querySelectorAll('input[name="widget-align"]')).forEach(function (input) {
    input.addEventListener('change', function () {
      var widget = selectedWidget();
      if (!widget) return;
      widget.align = input.value;
      renderLayoutEditor();
      commitLayoutChange();
    });
  });

  byId('layout-widget-opacity').addEventListener('input', function () {
    var widget = selectedWidget();
    if (!widget) return;
    widget.opacity = Number(byId('layout-widget-opacity').value);
    byId('layout-opacity-output').textContent = widget.opacity + '%';
    editor.update(dashboardLayout, previewRuntime());
  });
  byId('layout-widget-opacity').addEventListener('change', commitLayoutChange);

  byId('layout-note-text').addEventListener('input', function () {
    var widget = selectedWidget();
    if (!widget || widget.type !== 'note') return;
    widget.text = byId('layout-note-text').value;
    editor.update(dashboardLayout, previewRuntime());
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

  ['layout-widget-x', 'layout-widget-y', 'layout-widget-width', 'layout-widget-height'].forEach(function (id) {
    byId(id).addEventListener('change', updateWidgetGeometry);
  });

  byId('layout-widget-layer').addEventListener('change', function () {
    var widget = selectedWidget();
    if (!widget) return;
    widget.layer = clamp(Number(byId('layout-widget-layer').value), 0, 99);
    renderLayoutEditor();
    commitLayoutChange();
  });

  function changeWidgetLayer(delta) {
    var widget = selectedWidget();
    if (!widget) return;
    widget.layer = clamp(Number(widget.layer || 0) + delta, 0, 99);
    renderLayoutEditor();
    commitLayoutChange();
  }
  byId('layout-send-backward').addEventListener('click', function () { changeWidgetLayer(-1); });
  byId('layout-bring-forward').addEventListener('click', function () { changeWidgetLayer(1); });

  byId('layout-duplicate-widget').addEventListener('click', function () {
    var widget = selectedWidget();
    if (!widget || dashboardLayout.widgets.length >= 40) {
      setMessage('layout-message', 'The layout already holds 40 widgets.', true);
      return;
    }
    var duplicate = cloneValue(widget);
    duplicate.id = uniqueWidgetId(widget.type);
    duplicate.locked = false;
    duplicate.visible = true;
    duplicate.layer = clamp(Number(widget.layer || 0) + 1, 0, 99);
    duplicate.x = clamp(snapValue(widget.x + gridSize()), 0, 1000 - widget.w);
    duplicate.y = clamp(snapValue(widget.y + gridSize()), 0, 1000 - widget.h);
    dashboardLayout.widgets.push(duplicate);
    selectedWidgetId = duplicate.id;
    renderLayoutEditor();
    commitLayoutChange();
  });

  byId('layout-delete-widget').addEventListener('click', function () {
    var widget = selectedWidget();
    if (!widget || widget.id === widget.type || dashboardLayout.widgets.length <= 1) return;
    var index = dashboardLayout.widgets.indexOf(widget);
    dashboardLayout.widgets.splice(index, 1);
    selectedWidgetId = dashboardLayout.widgets[Math.max(0, index - 1)].id;
    renderLayoutEditor();
    commitLayoutChange();
  });

  byId('layout-undo').addEventListener('click', function () {
    if (!layoutHistory.length) return;
    layoutFuture.push(layoutText());
    applyLayoutSnapshot(layoutHistory.pop());
  });

  byId('layout-redo').addEventListener('click', function () {
    if (!layoutFuture.length) return;
    layoutHistory.push(layoutText());
    applyLayoutSnapshot(layoutFuture.pop());
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
    window.localStorage.setItem('mirror-layout-safe-zone', String(layoutSettings.safeZone));
    ensureCanvasChrome();
  });

  function closeMenu() {
    var menu = document.querySelector('.menu');
    if (menu) menu.removeAttribute('open');
  }
  document.addEventListener('click', function (event) {
    var menu = document.querySelector('.menu');
    if (menu && menu.hasAttribute('open') && !menu.contains(event.target)) closeMenu();
  });

  byId('layout-export').addEventListener('click', function () {
    closeMenu();
    if (!dashboardLayout) return;
    var blob = new Blob([JSON.stringify(dashboardLayout, null, 2) + '\n'], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = 'mirror-layout.json';
    link.click();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  });

  byId('layout-import').addEventListener('click', function () {
    closeMenu();
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
        request('/api/v1/dashboard/layout/validate', json('POST', imported)).then(function (normalized) {
          layoutHistory.push(layoutText());
          layoutFuture = [];
          dashboardLayout = normalized;
          selectedWidgetId = normalized.widgets[0].id;
          layoutBaseline = layoutText();
          renderLayoutEditor();
          updateHistoryButtons();
          setMessage('layout-message', 'Layout imported. Save to show it on the mirror.');
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
        savedLayout = cloneValue(saved);
        resetLayoutHistory();
        return request('/api/v1/dashboard', json('PUT', { url: '' }));
      })
      .then(function () {
        if (status) status.dashboardUrl = '';
        setRadio('source', 'native');
        byId('dashboard-url-row').classList.add('hidden');
        renderLayoutEditor();
        renderHomePreview();
        setMessage('layout-message', '');
        toast('Saved. The mirror is showing your layout.');
      })
      .catch(function (error) { setMessage('layout-message', error.message, true); });
  });

  byId('reset-dashboard-layout').addEventListener('click', function () {
    closeMenu();
    if (!window.confirm('Reset the layout to the default? Wi-Fi, photos, and schedules are kept.')) return;
    setMessage('layout-message', 'Restoring the default…');
    request('/api/v1/dashboard/layout/reset', json('POST', {}))
      .then(function (layout) {
        dashboardLayout = layout;
        savedLayout = cloneValue(layout);
        selectedWidgetId = 'clock';
        resetLayoutHistory();
        return request('/api/v1/dashboard', json('PUT', { url: '' }));
      })
      .then(function () {
        if (status) status.dashboardUrl = '';
        setRadio('source', 'native');
        renderLayoutEditor();
        renderHomePreview();
        setMessage('layout-message', '');
        toast('Default layout restored');
      })
      .catch(function (error) { setMessage('layout-message', error.message, true); });
  });

  /* ---------- Display: canvas gestures ---------- */

  (function enableLayoutGestures() {
    var canvas = byId('layout-preview');
    var gesture = null;

    function removeGuides() {
      Array.from(canvas.querySelectorAll('.mr-guide')).forEach(function (guide) { guide.remove(); });
    }

    function showGuide(axis, value) {
      var guide = document.createElement('div');
      guide.className = 'mr-guide ' + axis;
      if (axis === 'vertical') guide.style.left = (value / 10) + '%';
      else guide.style.top = (value / 10) + '%';
      canvas.appendChild(guide);
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
        resize ? [geometry.x + geometry.w] : [geometry.x, geometry.x + geometry.w / 2, geometry.x + geometry.w],
        targets.vertical);
      var horizontal = nearestAlignment(
        resize ? [geometry.y + geometry.h] : [geometry.y, geometry.y + geometry.h / 2, geometry.y + geometry.h],
        targets.horizontal);
      if (vertical) {
        if (resize) geometry.w += vertical.delta; else geometry.x += vertical.delta;
        showGuide('vertical', vertical.target);
      }
      if (horizontal) {
        if (resize) geometry.h += horizontal.delta; else geometry.y += horizontal.delta;
        showGuide('horizontal', horizontal.target);
      }
      geometry.w = clamp(geometry.w, 24, 1000 - geometry.x);
      geometry.h = clamp(geometry.h, 24, 1000 - geometry.y);
      geometry.x = clamp(geometry.x, 0, 1000 - geometry.w);
      geometry.y = clamp(geometry.y, 0, 1000 - geometry.h);
      return geometry;
    }

    canvas.addEventListener('pointerdown', function (event) {
      var element = event.target.closest('.mr-widget');
      if (!element || !dashboardLayout) return;
      selectedWidgetId = element.getAttribute('data-widget-id');
      var widget = selectedWidget();
      if (!widget) return;
      editor.setSelected(selectedWidgetId);
      renderLayers();
      updateWidgetControls();
      if (widget.locked) return;
      var bounds = canvas.getBoundingClientRect();
      layoutGestureActive = true;
      gesture = {
        pointerId: event.pointerId,
        element: element,
        resize: event.target.classList.contains('mr-handle'),
        startX: event.clientX,
        startY: event.clientY,
        canvasWidth: bounds.width,
        canvasHeight: bounds.height,
        x: widget.x, y: widget.y, w: widget.w, h: widget.h,
        moved: false
      };
      canvas.setPointerCapture(event.pointerId);
      event.preventDefault();
    });

    canvas.addEventListener('pointermove', function (event) {
      if (!gesture || event.pointerId !== gesture.pointerId) return;
      var widget = selectedWidget();
      if (!widget) return;
      var deltaX = Math.round((event.clientX - gesture.startX) / gesture.canvasWidth * 1000);
      var deltaY = Math.round((event.clientY - gesture.startY) / gesture.canvasHeight * 1000);
      if (!gesture.moved && Math.abs(deltaX) < 4 && Math.abs(deltaY) < 4) return;
      gesture.moved = true;
      var geometry = { x: gesture.x, y: gesture.y, w: gesture.w, h: gesture.h };
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
      byId('layout-widget-x').value = String(widget.x);
      byId('layout-widget-y').value = String(widget.y);
      byId('layout-widget-width').value = String(widget.w);
      byId('layout-widget-height').value = String(widget.h);
      event.preventDefault();
    });

    function endGesture(event) {
      if (!gesture || event.pointerId !== gesture.pointerId) return;
      if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      var moved = gesture.moved;
      gesture = null;
      layoutGestureActive = false;
      removeGuides();
      renderLayoutEditor();
      if (moved) commitLayoutChange();
    }

    canvas.addEventListener('pointerup', endGesture);
    canvas.addEventListener('pointercancel', endGesture);

    canvas.addEventListener('keydown', function (event) {
      var element = event.target.closest('.mr-widget');
      if (!element || ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].indexOf(event.key) < 0) return;
      selectedWidgetId = element.getAttribute('data-widget-id');
      var widget = selectedWidget();
      if (!widget || widget.locked) return;
      var step = gridSize();
      var horizontal = event.key === 'ArrowLeft' ? -step : (event.key === 'ArrowRight' ? step : 0);
      var vertical = event.key === 'ArrowUp' ? -step : (event.key === 'ArrowDown' ? step : 0);
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
      var focused = editor.element(widget.id);
      if (focused) focused.focus();
    });
  }());

  /* ---------- Display: weather ---------- */

  function enableWeatherWidgetIfNeeded() {
    if (!dashboardLayout || !byId('weather-enabled').checked) return Promise.resolve();
    var weatherWidget = dashboardLayout.widgets.find(function (widget) { return widget.type === 'weather'; });
    if (!weatherWidget || weatherWidget.visible) return Promise.resolve();
    var overlapsVisibleWidget = dashboardLayout.widgets.some(function (other) {
      if (other.id === weatherWidget.id || !other.visible) return false;
      return weatherWidget.x < other.x + other.w && weatherWidget.x + weatherWidget.w > other.x
        && weatherWidget.y < other.y + other.h && weatherWidget.y + weatherWidget.h > other.y;
    });
    if (overlapsVisibleWidget) {
      weatherWidget.x = 600;
      weatherWidget.y = 58;
      weatherWidget.w = 350;
      weatherWidget.h = 110;
    }
    weatherWidget.visible = true;
    commitLayoutChange();
    return request('/api/v1/dashboard/layout', json('PUT', dashboardLayout)).then(function (normalized) {
      dashboardLayout = normalized;
      savedLayout = cloneValue(normalized);
      resetLayoutHistory();
      renderLayoutEditor();
      renderHomePreview();
    });
  }

  byId('weather-form').addEventListener('submit', function (event) {
    event.preventDefault();
    var enabled = byId('weather-enabled').checked;
    var body = {
      enabled: enabled,
      locationName: byId('weather-location-name').value.trim(),
      units: radioValue('weather-units') || 'us'
    };
    if (enabled) {
      var latitudeValue = byId('weather-latitude').value.trim();
      var longitudeValue = byId('weather-longitude').value.trim();
      if (!latitudeValue || !longitudeValue) {
        setMessage('weather-message', 'Search for a place or enter coordinates first.', true);
        return;
      }
      body.latitude = Number(latitudeValue);
      body.longitude = Number(longitudeValue);
    }
    setMessage('weather-message', 'Saving…');
    request('/api/v1/weather', json('PUT', body))
      .then(function (weather) {
        weatherSnapshot = weather;
        return enableWeatherWidgetIfNeeded();
      })
      .then(function () {
        if (!enabled) {
          setMessage('weather-message', '');
          toast('Weather hidden');
          return refreshStatus();
        }
        setMessage('weather-message', 'Saved. Fetching the forecast…');
        return request('/api/v1/weather/refresh', json('POST', {})).then(function () {
          weatherPollTimer = window.setTimeout(function () {
            pollWeatherUntilSettled(12).catch(function (error) {
              setMessage('weather-message', error.message, true);
            });
          }, 1000);
        });
      })
      .catch(function (error) { setMessage('weather-message', error.message, true); });
  });

  byId('weather-use-location').addEventListener('click', function () {
    if (!window.isSecureContext || !navigator.geolocation) {
      setMessage('weather-message', 'Browser location needs HTTPS or localhost. Search for a place instead.', true);
      return;
    }
    setMessage('weather-message', 'Asking this device for its location…');
    navigator.geolocation.getCurrentPosition(function (position) {
      byId('weather-latitude').value = position.coords.latitude.toFixed(5);
      byId('weather-longitude').value = position.coords.longitude.toFixed(5);
      if (!byId('weather-location-name').value.trim()) byId('weather-location-name').value = 'Home';
      byId('weather-enabled').checked = true;
      setMessage('weather-message', 'Location filled in. Save weather to apply it.');
    }, function (error) {
      setMessage('weather-message', error.message || 'Unable to read this device\u2019s location.', true);
    }, { enableHighAccuracy: false, timeout: 15000, maximumAge: 10 * 60 * 1000 });
  });

  function searchWeatherPlaces() {
    var query = byId('weather-place-search').value.trim();
    if (query.length < 2) {
      setMessage('weather-message', 'Type at least two characters.', true);
      return;
    }
    setMessage('weather-message', 'Searching…');
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
        selector.classList.toggle('hidden', !selector.options.length);
        if (!selector.options.length) {
          setMessage('weather-message', 'No matching places were found.', true);
          return;
        }
        selector.dispatchEvent(new Event('change'));
        setMessage('weather-message', 'Place selected. Save weather to apply it.');
      })
      .catch(function (error) { setMessage('weather-message', error.message, true); });
  }

  byId('weather-search').addEventListener('click', searchWeatherPlaces);
  byId('weather-place-search').addEventListener('keydown', function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      searchWeatherPlaces();
    }
  });

  byId('weather-search-results').addEventListener('change', function () {
    var option = byId('weather-search-results').selectedOptions[0];
    if (!option) return;
    byId('weather-latitude').value = Number(option.dataset.latitude).toFixed(5);
    byId('weather-longitude').value = Number(option.dataset.longitude).toFixed(5);
    byId('weather-location-name').value = option.dataset.label;
    byId('weather-place-search').value = option.dataset.label;
    byId('weather-enabled').checked = true;
  });

  if (!window.isSecureContext || !navigator.geolocation) {
    byId('weather-use-location').disabled = true;
    byId('weather-use-location').title = 'Browser location needs HTTPS or localhost.';
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
      .catch(function (error) { setMessage('weather-message', error.message, true); });
  });

  /* ---------- Display: photos ---------- */

  byId('photo-add').addEventListener('click', function () { byId('photo-file').click(); });
  byId('photo-file').addEventListener('change', function () {
    var file = byId('photo-file').files[0];
    if (!file) return;
    setMessage('photo-message', 'Uploading ' + file.name + '…');
    fetch('/api/v1/photos/' + encodeURIComponent(file.name), {
      method: 'PUT',
      headers: { Authorization: 'Bearer ' + token, 'Content-Type': file.type || 'image/jpeg' },
      body: file
    }).then(function (response) {
      return response.text().then(function (text) {
        var body = text ? JSON.parse(text) : {};
        if (!response.ok) throw new Error(body.error || 'Upload failed (' + response.status + ')');
        return body;
      });
    }).then(function () {
      byId('photo-file').value = '';
      setMessage('photo-message', '');
      toast('Photo added');
      return refreshPhotos();
    }).catch(function (error) { setMessage('photo-message', error.message, true); });
  });

  /* ---------- Schedule ---------- */

  byId('wake-brightness').addEventListener('input', function () {
    byId('wake-brightness-output').textContent = Math.round(Number(byId('wake-brightness').value) / 255 * 100) + '%';
  });
  byId('motion-sensitivity').addEventListener('input', function () {
    byId('motion-sensitivity-output').textContent = byId('motion-sensitivity').value;
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
      setMessage('automation-message', '');
      toast('Schedule saved');
      return refreshAutomation().then(refreshStatus);
    }).catch(function (error) { setMessage('automation-message', error.message, true); });
  });

  /* ---------- Settings ---------- */

  byId('name-form').addEventListener('submit', function (event) {
    event.preventDefault();
    request('/api/v1/control/name', json('POST', { name: byId('display-name').value.trim() }))
      .then(function () {
        setMessage('display-message', '');
        toast('Name saved');
        byId('display-name').blur();
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
      setMessage('clock-message', '');
      toast('Clock saved');
      return refreshStatus();
    }).catch(function (error) { setMessage('clock-message', error.message, true); });
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
          + '/#handoff=' + encodeURIComponent(token) + '&clientId=' + encodeURIComponent(clientId);
        return;
      }
      window.setTimeout(function () { refreshStatus().catch(function () {}); }, 4000);
    }).catch(function (error) {
      byId('wifi-passphrase').value = '';
      setMessage('wifi-message', error.message, true);
    });
  });

  byId('start-setup-network').addEventListener('click', function () {
    request('/api/v1/onboarding/start', json('POST', {}))
      .then(function () {
        setMessage('onboarding-message', 'Starting the recovery network…');
        window.setTimeout(function () { refreshOnboarding().then(function () { setMessage('onboarding-message', ''); }); }, 3000);
      })
      .catch(function (error) { setMessage('onboarding-message', error.message, true); });
  });

  byId('stop-setup-network').addEventListener('click', function () {
    request('/api/v1/onboarding/stop', json('POST', {}))
      .then(function () { return refreshOnboarding(); })
      .catch(function (error) { setMessage('onboarding-message', error.message, true); });
  });

  byId('refresh-clients').addEventListener('click', function () {
    refreshClients().catch(function (error) { setMessage('access-message', error.message, true); });
  });

  byId('forget-this-device').addEventListener('click', function () {
    if (!window.confirm('Forget this browser? You will need the pairing code to connect again.')) return;
    request('/api/v1/pair/revoke', json('POST', {}))
      .catch(function () { return null; })
      .then(forgetLocalCredential);
  });

  /* ---------- Boot ---------- */

  if (!byId('time-zone').value) {
    byId('time-zone').value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  }
  showPairedState(Boolean(token));
  refreshAll();
  alignedTick();
  window.setInterval(function () {
    if (token && !document.hidden) refreshStatus().catch(function () {});
  }, 10000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && token) refreshStatus().catch(function () {});
  });
  var resizeTimer = null;
  window.addEventListener('resize', function () {
    if (resizeTimer) window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(function () {
      if (dashboardLayout) editor.relayout();
      if (savedLayout) homeRenderer.relayout();
    }, 120);
  });
}());
