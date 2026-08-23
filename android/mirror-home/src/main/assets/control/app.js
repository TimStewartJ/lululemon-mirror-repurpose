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
  var selectedWidgetId = 'clock';
  var layoutPhotoObjectUrl = null;
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
    byId('layout-note-row').classList.toggle('hidden', widget.type !== 'note');
    byId('layout-note-text').value = widget.text || '';
  }

  function renderLayoutEditor() {
    if (!dashboardLayout) return;
    var preview = byId('layout-preview');
    Array.from(preview.querySelectorAll('.layout-widget')).forEach(function (element) {
      element.remove();
    });
    updateLayoutPreviewBackground();
    dashboardLayout.widgets.forEach(function (widget) {
      var element = document.createElement('div');
      element.className = 'layout-widget layout-widget-' + widget.type +
        ' align-' + widget.align +
        (widget.id === selectedWidgetId ? ' selected' : '') +
        (!widget.visible ? ' is-hidden' : '');
      element.dataset.widgetId = widget.id;
      element.tabIndex = 0;
      element.setAttribute('role', 'button');
      element.setAttribute('aria-label', (widgetLabels[widget.id] || widget.id) + ' widget');
      element.style.left = (widget.x / 10) + '%';
      element.style.top = (widget.y / 10) + '%';
      element.style.width = (widget.w / 10) + '%';
      element.style.height = (widget.h / 10) + '%';
      element.style.opacity = String(widget.opacity / 100);
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
  }

  function refreshDashboardLayout() {
    if (!token) return Promise.resolve();
    return request('/api/v1/dashboard/layout').then(function (layout) {
      dashboardLayout = layout;
      if (!selectedWidget()) selectedWidgetId = 'clock';
      var selector = byId('layout-widget-select');
      selector.textContent = '';
      dashboardLayout.widgets.forEach(function (widget) {
        var option = document.createElement('option');
        option.value = widget.id;
        option.textContent = widgetLabels[widget.id] || widget.id;
        selector.appendChild(option);
      });
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
      byId('automation-capability').textContent = automation.ambientLightAvailable
        ? 'Ambient light sensor available.'
        : 'No ambient-light sensor was detected; time-based brightness remains available.';
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

  function updateLayoutBackground() {
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
  });

  byId('layout-widget-align').addEventListener('change', function () {
    var widget = selectedWidget();
    if (!widget) return;
    widget.align = byId('layout-widget-align').value;
    renderLayoutEditor();
  });

  byId('layout-widget-opacity').addEventListener('input', function () {
    var widget = selectedWidget();
    if (!widget) return;
    widget.opacity = Number(byId('layout-widget-opacity').value);
    byId('layout-opacity-output').textContent = widget.opacity + '%';
    renderLayoutEditor();
  });

  byId('layout-note-text').addEventListener('input', function () {
    var widget = selectedWidget();
    if (!widget || widget.type !== 'note') return;
    widget.text = byId('layout-note-text').value;
    renderLayoutEditor();
  });

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
  }

  [
    'layout-widget-x',
    'layout-widget-y',
    'layout-widget-width',
    'layout-widget-height'
  ].forEach(function (id) {
    byId(id).addEventListener('change', updateWidgetGeometry);
  });

  byId('save-dashboard-layout').addEventListener('click', function () {
    if (!dashboardLayout) return;
    setMessage('layout-message', 'Saving…');
    request('/api/v1/dashboard/layout', json('PUT', dashboardLayout))
      .then(function (saved) {
        dashboardLayout = saved;
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

    function clamp(value, minimum, maximum) {
      return Math.max(minimum, Math.min(maximum, value));
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
      var bounds = preview.getBoundingClientRect();
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
      if (gesture.resize) {
        widget.w = clamp(gesture.w + deltaX, 24, 1000 - widget.x);
        widget.h = clamp(gesture.h + deltaY, 24, 1000 - widget.y);
      } else {
        widget.x = clamp(gesture.x + deltaX, 0, 1000 - widget.w);
        widget.y = clamp(gesture.y + deltaY, 0, 1000 - widget.h);
      }
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
      renderLayoutEditor();
    }

    preview.addEventListener('pointerup', endGesture);
    preview.addEventListener('pointercancel', endGesture);
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
