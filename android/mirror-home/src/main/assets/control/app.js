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
  var status = null;
  var savedLayout = null;
  var dashboardLayout = null;
  var weatherSnapshot = null;
  var photoList = [];
  var backgroundVideoCatalog = {
    videos: [],
    activeId: '',
    previousId: '',
    canRollback: false
  };
  var noteList = [];
  var voiceReport = null;
  var voiceBusy = false;
  var scanGuardBusy = false;
  var assistantReport = null;
  var assistantBusy = false;
  var assistantEdited = false;
  var assistantAsking = false;
  var notesVersion = null;
  var noteLimit = 1000;
  var editingNoteId = '';
  var boardSummary = null;
  var boardEverything = [];
  var boardVersion = null;
  var pendingBackgroundVideoId = '';
  var videoScheduleDraft = null;
  var videoScheduleDirty = false;
  var selectedWidgetId = 'clock';
  var layoutHistory = [];
  var layoutFuture = [];
  var layoutBaseline = '';
  var layoutGestureActive = false;
  var weatherPollTimer = null;
  var toastTimer = null;
  var photoUrlCache = {};
  var videoPosterUrlCache = {};
  var layoutSettings = {
    snap: window.localStorage.getItem('mirror-layout-snap') !== 'false',
    grid: Number(window.localStorage.getItem('mirror-layout-grid') || 20)
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
    uptime: 'Uptime',
    motion: 'Presence',
    weather: 'Weather',
    forecast: 'Hourly forecast',
    pairing: 'Pairing code',
    note: 'Note',
    photo: 'Photo',
    board: 'Board'
  };
  var noteSourceLabels = [
    ['latest', 'Newest note'],
    ['rotate', 'Rotate through notes'],
    ['list', 'All notes'],
    ['text', 'Fixed text']
  ];

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

  function formatBytes(value) {
    var bytes = Math.max(0, Number(value || 0));
    if (bytes < 1024) return Math.round(bytes) + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KiB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MiB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GiB';
  }

  function formatDuration(milliseconds) {
    var seconds = Math.max(0, Math.round(Number(milliseconds || 0) / 1000));
    var minutes = Math.floor(seconds / 60);
    var remainder = seconds % 60;
    return minutes + ':' + (remainder < 10 ? '0' : '') + remainder;
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

  /* Fetch the smallest authenticated variant that fills the box, as a blob URL. */
  function resolvePhoto(name, done, box) {
    var edge = box ? Math.max(box.width, box.height) * (window.devicePixelRatio || 1) : Infinity;
    var variant = edge <= 320 ? '/thumbnail' : '/display';
    var key = name + variant;
    if (photoUrlCache[key]) { done(photoUrlCache[key]); return; }
    fetch('/api/v1/photos/' + encodeURIComponent(name) + variant, {
      headers: { Authorization: 'Bearer ' + token }
    }).then(function (response) {
      if (!response.ok) throw new Error('Photo unavailable');
      return response.blob();
    }).then(function (blob) {
      photoUrlCache[key] = URL.createObjectURL(blob);
      done(photoUrlCache[key]);
    }).catch(function () { done(''); });
  }

  function listPhotos(done) {
    done(photoList.map(function (photo) { return photo.name; }));
  }

  function resolveVideoPoster(id, done) {
    if (!id) { done(''); return; }
    if (videoPosterUrlCache[id]) { done(videoPosterUrlCache[id]); return; }
    fetch('/api/v1/background-videos/' + encodeURIComponent(id) + '/poster', {
      headers: { Authorization: 'Bearer ' + token }
    }).then(function (response) {
      if (!response.ok) throw new Error('Video poster unavailable');
      return response.blob();
    }).then(function (blob) {
      videoPosterUrlCache[id] = URL.createObjectURL(blob);
      done(videoPosterUrlCache[id]);
    }).catch(function () { done(''); });
  }

  var rendererOptions = {
    resolvePhoto: resolvePhoto,
    listPhotos: listPhotos,
    resolveVideoPoster: resolveVideoPoster
  };
  var homeRenderer = window.MirrorRenderer.create(byId('home-preview'), rendererOptions);
  var editor = window.MirrorRenderer.create(byId('layout-preview'), {
    editing: true,
    resolvePhoto: resolvePhoto,
    listPhotos: listPhotos,
    resolveVideoPoster: resolveVideoPoster
  });

  function previewRuntime() {
    var runtime = status || {};
    runtime.notes = noteList;
    runtime.board = boardSummary;
    return runtime;
  }

  function renderHomePreview() {
    var note = byId('home-preview-note');
    var url = status && status.dashboardUrl;
    renderBoardNotice();
    if (url) {
      note.classList.remove('hidden');
      note.textContent = 'A web page is showing on the mirror';
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

  /* ---------- Notes helpers ---------- */

  function notePreview(text, length) {
    var flat = String(text || '').replace(/\s+/g, ' ').trim();
    return flat.length > length ? flat.slice(0, length - 1) + '\u2026' : flat;
  }

  /* Grow with the text from a single line; past max-height the textarea
     scrolls. scrollHeight excludes the border, so add it back or the box
     ends up a hair short and clips the last line. */
  function autosize(textarea) {
    textarea.style.height = 'auto';
    var border = textarea.offsetHeight - textarea.clientHeight;
    textarea.style.height = (textarea.scrollHeight + border) + 'px';
  }

  function updateNoteCount(textarea, output) {
    var length = textarea.value.length;
    output.textContent = length + ' / ' + noteLimit;
    output.classList.toggle('near', length >= noteLimit * 0.9);
  }

  function renderNoteSourceOptions(widget) {
    var select = byId('layout-note-source');
    var current = widget.source === 'pinned' ? 'pinned:' + (widget.note || '') : (widget.source || 'text');
    select.textContent = '';
    noteSourceLabels.forEach(function (entry) {
      var option = document.createElement('option');
      option.value = entry[0];
      option.textContent = entry[1];
      select.appendChild(option);
    });
    var found = false;
    noteList.forEach(function (note) {
      var option = document.createElement('option');
      option.value = 'pinned:' + note.id;
      option.textContent = 'Always: ' + notePreview(note.text, 40);
      if (option.value === current) found = true;
      select.appendChild(option);
    });
    if (widget.source === 'pinned' && !found) {
      var missing = document.createElement('option');
      missing.value = current;
      missing.textContent = 'A deleted note';
      select.appendChild(missing);
    }
    select.value = current;
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
    var isNote = widget.type === 'note';
    Array.from(document.querySelectorAll('.note-only')).forEach(function (row) {
      row.classList.toggle('hidden', !isNote);
    });
    byId('layout-note-row').classList.toggle('hidden', !isNote || (widget.source || 'text') !== 'text');
    if (isNote) {
      renderNoteSourceOptions(widget);
      if (document.activeElement !== byId('layout-note-text')) {
        byId('layout-note-text').value = widget.text || '';
        autosize(byId('layout-note-text'));
      }
      updateNoteCount(byId('layout-note-text'), byId('layout-note-count'));
      setRadio('note-size', widget.size || 'auto');
      setRadio('note-weight', widget.weight || 'light');
    }
    Array.from(document.querySelectorAll('.photo-only')).forEach(function (row) {
      row.classList.toggle('hidden', widget.type !== 'photo');
    });
    Array.from(document.querySelectorAll('.board-only')).forEach(function (row) {
      row.classList.toggle('hidden', widget.type !== 'board');
    });
    if (widget.type === 'board') {
      if (document.activeElement !== byId('layout-board-heading')) {
        byId('layout-board-heading').value = widget.text || '';
      }
      byId('layout-board-show').value = widget.show || 'all';
      setRadio('board-size', widget.size || 'medium');
    }
    if (widget.type === 'photo') {
      byId('layout-widget-photo').value = widget.photo || '';
      setRadio('widget-fit', widget.fit || 'cover');
    }
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
    byId('layout-background-video').value =
      pendingBackgroundVideoId || showingVideoId();
    setRadio('background-fit', dashboardLayout.background.fit || 'cover');
    byId('layout-secondary-row').classList.toggle('hidden', dashboardLayout.background.mode !== 'gradient');
    byId('layout-background-photo-row').classList.toggle('hidden', dashboardLayout.background.mode !== 'photo');
    byId('layout-background-video-row').classList.toggle('hidden', dashboardLayout.background.mode !== 'video');
    byId('layout-background-fit-row').classList.toggle('hidden', dashboardLayout.background.mode !== 'video');
    byId('layout-dim-row').classList.toggle(
      'hidden',
      dashboardLayout.background.mode !== 'photo' && dashboardLayout.background.mode !== 'video');
    renderLayers();
    updateWidgetControls();
    byId('layout-snap-enabled').checked = layoutSettings.snap;
    byId('layout-grid-size').value = String(gridSize());
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
          /* Older Mirror Home versions do not report this and always accept codes. */
          byId('pairing-lede').textContent = next.pairingOpen === false
            ? 'The mirror is not showing a pairing code right now. On a device that is already '
              + 'paired, open Settings, then Paired devices, and choose Show code.'
            : 'Enter the six-digit code on the mirror. This browser gets its own key, '
              + 'which you can revoke any time.';
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
        var address = next.address || (next.wifi && next.wifi.ipAddress) || '';
        byId('about-address').textContent = address ? address + ':8787' : 'USB only';
        byId('about-binder').textContent = next.mirrorBinderConnected ? 'Connected' : 'Reconnecting';
        byId('about-helper').textContent = next.systemHelperConnected ? 'Temporary helper active' : 'Factory service';
        byId('about-uptime').textContent = formatUptime(next.deviceUptimeSeconds);
        var restart = next.restart || {};
        byId('about-restart').classList.toggle('hidden', !restart.advised);
        byId('about-restart').textContent = restart.advised
          ? restart.reason + '. Switch the Mirror off and on when convenient; only that gives the memory back.'
          : '';
        if (next.weather) {
          weatherSnapshot = next.weather;
          renderWeatherStatus(next.weather);
        }
        if (next.automation) renderMotionStatus(next.automation);
        renderVoiceSummary(next.voice);
        renderAssistantSummary(next.assistant);
        renderScanGuard(next.wifi && next.wifi.scanGuard);
        renderHomeStatus();
        renderNowPlaying();
        renderHomePreview();
        if (dashboardLayout && !layoutGestureActive) editor.update(dashboardLayout, previewRuntime());
        showPairedState(true);
        if (typeof next.notesVersion === 'number' && notesVersion !== null && next.notesVersion !== notesVersion) {
          refreshNotes().catch(function () {});
        }
        if (typeof next.boardVersion === 'number' && boardVersion !== null && next.boardVersion !== boardVersion) {
          refreshBoard().catch(function () {});
        }
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
    return url ? 'custom' : 'native';
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
      var zone = preferences.timeZone || window.MirrorClock.browserZone();
      byId('time-zone').value = zone;
      byId('clock-24-hour').checked = Boolean(preferences.clock24Hour);
      renderClockHint(preferences);
      /* The browser has current time-zone rules; top up the Mirror whenever
         its saved offset or upcoming changes differ. */
      var clock = clockFor(zone);
      if (!clock || window.MirrorClock.matches(preferences, clock)) return;
      return request('/api/v1/preferences', json('PUT', {
        timeZone: zone,
        utcOffsetMinutes: clock.utcOffsetMinutes,
        utcOffsetChanges: clock.utcOffsetChanges,
        clock24Hour: Boolean(preferences.clock24Hour)
      })).then(renderClockHint);
    });
  }

  /* Null when this browser cannot resolve the zone. */
  function clockFor(zone) {
    try {
      return window.MirrorClock.describe(zone);
    } catch (error) {
      return null;
    }
  }

  function renderClockHint(preferences) {
    var hint = byId('clock-hint');
    var next = preferences && (preferences.utcOffsetChanges || [])[0];
    if (!next) {
      hint.textContent = 'No clock changes are scheduled for this time zone.';
      return;
    }
    var before = Number(preferences.utcOffsetMinutes || 0);
    var shift = Number(next.utcOffsetMinutes) - before;
    var size = Math.abs(shift);
    var amount = [];
    if (size >= 60) amount.push(Math.floor(size / 60) + (size < 120 ? ' hour' : ' hours'));
    if (size % 60) amount.push((size % 60) + ' minutes');
    /* The wall time just before the change, read off a UTC-shifted date. */
    var local = new Date(Number(next.at) + before * 60000);
    var hours = local.getUTCHours();
    var minutes = local.getUTCMinutes();
    hint.textContent = 'The mirror moves its clock ' + (shift > 0 ? 'forward ' : 'back ')
      + amount.join(' ') + ' on '
      + local.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' })
      + ' at ' + formatClockTime((hours < 10 ? '0' : '') + hours + ':' + (minutes < 10 ? '0' : '') + minutes)
      + '.';
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
      var frameSelect = byId('layout-widget-photo');
      var selectedBackground = dashboardLayout ? dashboardLayout.background.photo : '';
      var selected = selectedWidget();
      var selectedFrame = selected && selected.type === 'photo' ? selected.photo || '' : '';
      var framed = {};
      if (dashboardLayout) {
        dashboardLayout.widgets.forEach(function (widget) {
          if (widget.type === 'photo' && widget.visible && widget.photo) framed[widget.photo] = true;
        });
      }
      backgroundSelect.textContent = '';
      frameSelect.textContent = '';
      var noPhoto = document.createElement('option');
      noPhoto.value = '';
      noPhoto.textContent = 'Choose a photo';
      backgroundSelect.appendChild(noPhoto);
      var rotate = document.createElement('option');
      rotate.value = '';
      rotate.textContent = 'Rotate through the library';
      frameSelect.appendChild(rotate);
      photoList.forEach(function (photo) {
        var option = document.createElement('option');
        option.value = photo.name;
        option.textContent = photo.name;
        backgroundSelect.appendChild(option);
        frameSelect.appendChild(option.cloneNode(true));

        var tile = document.createElement('figure');
        tile.className = 'photo-tile' +
          (photo.name === selectedBackground || framed[photo.name] ? ' in-use' : '');
        tile.style.margin = '0';
        var image = document.createElement('img');
        image.alt = photo.name;
        image.loading = 'lazy';
        image.title = 'Place in a frame';
        resolvePhoto(photo.name, function (url) {
          if (!url) return;
          image.onload = function () { image.classList.add('ready'); };
          image.src = url;
        }, { width: 160, height: 160 });
        image.addEventListener('click', function () { placePhotoInFrame(photo.name); });
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
      frameSelect.value = selectedFrame;
      editor.refreshPhotos();
      homeRenderer.refreshPhotos();
    });
  }

  function findBackgroundVideo(id) {
    var videos = backgroundVideoCatalog.videos || [];
    for (var index = 0; index < videos.length; index++) {
      if (videos[index].id === id) return videos[index];
    }
    return null;
  }

  function syncBackgroundVideoStatus() {
    if (!status) return;
    status.backgroundVideos = {
      active: findBackgroundVideo(showingVideoId()),
      previous: findBackgroundVideo(backgroundVideoCatalog.previousId),
      canRollback: Boolean(backgroundVideoCatalog.canRollback)
    };
  }

  function showingVideoId() {
    return backgroundVideoCatalog.effectiveId || backgroundVideoCatalog.activeId || '';
  }

  function scheduleRunning() {
    return Boolean(backgroundVideoCatalog.schedule && backgroundVideoCatalog.schedule.active);
  }

  function videoLabel(id) {
    var video = findBackgroundVideo(id);
    return video ? video.name.replace(/\.mp4$/i, '') : 'a removed video';
  }

  /* Times on the mirror follow its saved UTC offset, not this browser's zone. */
  function formatMirrorTime(epoch) {
    var offset = Number((backgroundVideoCatalog.schedule || {}).utcOffsetMinutes || 0);
    var local = new Date(Number(epoch) + offset * 60000);
    var hours = local.getUTCHours();
    var minutes = local.getUTCMinutes();
    return formatClockTime((hours < 10 ? '0' : '') + hours + ':' + (minutes < 10 ? '0' : '') + minutes);
  }

  /* The Mirror reports the hold's local end time itself, which stays right
     across a daylight-saving change. */
  function formatHoldEnd(hold) {
    return hold.untilTime ? formatClockTime(hold.untilTime) : formatMirrorTime(hold.until);
  }

  function videoScheduleSummary() {
    var schedule = backgroundVideoCatalog.schedule;
    if (!schedule || !schedule.active) {
      return schedule && schedule.slots && schedule.slots.length ? 'Off \u00b7 times are kept' : 'Off';
    }
    if (schedule.hold) {
      return 'Showing ' + videoLabel(schedule.hold.videoId) + ' until '
        + formatHoldEnd(schedule.hold) + ', then the schedule resumes';
    }
    var current = schedule.current;
    var next = schedule.next;
    if (!current || !next || next.videoId === current.videoId) {
      return 'Now: ' + videoLabel(current ? current.videoId : '');
    }
    return 'Now: ' + videoLabel(current.videoId) + ' \u00b7 ' + videoLabel(next.videoId)
      + ' at ' + formatClockTime(next.start);
  }

  function draftFromCatalog() {
    var schedule = backgroundVideoCatalog.schedule || {};
    return {
      enabled: Boolean(schedule.enabled),
      slots: (schedule.slots || []).map(function (slot) {
        return { start: slot.start, videoId: slot.videoId };
      })
    };
  }

  function currentScheduleDraft() {
    if (!videoScheduleDraft) videoScheduleDraft = draftFromCatalog();
    return videoScheduleDraft;
  }

  function renderVideoSchedule() {
    if (!videoScheduleDirty) videoScheduleDraft = draftFromCatalog();
    var draft = currentScheduleDraft();
    var videos = backgroundVideoCatalog.videos || [];
    var schedule = backgroundVideoCatalog.schedule || {};
    byId('video-schedule-enabled').checked = draft.enabled;
    var container = byId('video-schedule-slots');
    container.textContent = '';
    draft.slots.forEach(function (slot, index) {
      var row = document.createElement('div');
      row.className = 'row schedule-slot';

      var time = document.createElement('input');
      time.type = 'time';
      time.className = 'time';
      time.required = true;
      time.value = slot.start || '';
      time.setAttribute('aria-label', 'Starts at');
      time.addEventListener('change', function () {
        slot.start = time.value;
        videoScheduleDirty = true;
      });

      var select = document.createElement('select');
      select.className = 'select';
      select.setAttribute('aria-label', 'Video from this time');
      var placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = videos.length ? 'Choose a video' : 'Upload a video first';
      select.appendChild(placeholder);
      videos.forEach(function (video) {
        var option = document.createElement('option');
        option.value = video.id;
        option.textContent = video.name;
        select.appendChild(option);
      });
      select.value = findBackgroundVideo(slot.videoId) ? slot.videoId : '';
      select.addEventListener('change', function () {
        slot.videoId = select.value;
        videoScheduleDirty = true;
      });

      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'btn small';
      remove.textContent = 'Remove';
      remove.addEventListener('click', function () {
        draft.slots.splice(index, 1);
        videoScheduleDirty = true;
        renderVideoSchedule();
      });

      row.appendChild(time);
      row.appendChild(select);
      row.appendChild(remove);
      container.appendChild(row);
    });
    byId('video-schedule-add').disabled =
      !videos.length || draft.slots.length >= Number(schedule.maxSlots || 8);
    byId('video-schedule-resume').classList.toggle('hidden', !schedule.hold);
    byId('video-schedule-summary').textContent = videoScheduleSummary();
  }

  function refreshBackgroundVideos() {
    if (!token) return Promise.resolve();
    return request('/api/v1/background-videos').then(function (catalog) {
      backgroundVideoCatalog = catalog || backgroundVideoCatalog;
      syncBackgroundVideoStatus();

      var videos = backgroundVideoCatalog.videos || [];
      var select = byId('layout-background-video');
      select.textContent = '';
      var emptyOption = document.createElement('option');
      emptyOption.value = '';
      emptyOption.textContent = videos.length ? 'Choose a video' : 'Upload a video first';
      select.appendChild(emptyOption);
      videos.forEach(function (video) {
        var option = document.createElement('option');
        option.value = video.id;
        option.textContent = video.name;
        select.appendChild(option);
      });
      if (pendingBackgroundVideoId && !findBackgroundVideo(pendingBackgroundVideoId)) {
        pendingBackgroundVideoId = '';
      }
      select.value = pendingBackgroundVideoId || showingVideoId();

      var list = byId('background-video-list');
      list.textContent = '';
      if (!videos.length) {
        var empty = document.createElement('p');
        empty.className = 'video-empty';
        empty.textContent = 'No background videos yet.';
        list.appendChild(empty);
      }
      videos.forEach(function (video) {
        var card = document.createElement('article');
        card.className = 'video-card';

        var poster = document.createElement('div');
        poster.className = 'video-poster';
        poster.textContent = 'MP4';
        if (video.posterAvailable) {
          resolveVideoPoster(video.id, function (url) {
            if (!url) return;
            var image = document.createElement('img');
            image.alt = '';
            image.src = url;
            poster.textContent = '';
            poster.appendChild(image);
          });
        }

        var details = document.createElement('div');
        var name = document.createElement('strong');
        name.textContent = video.name;
        name.title = video.name;
        var metadata = document.createElement('small');
        var orientedWidth = video.rotation === 90 || video.rotation === 270
          ? video.height : video.width;
        var orientedHeight = video.rotation === 90 || video.rotation === 270
          ? video.width : video.height;
        metadata.textContent = orientedWidth + '\u00d7' + orientedHeight
          + ' \u00b7 ' + Math.round(Number(video.frameRate || 0)) + ' FPS'
          + ' \u00b7 ' + formatDuration(video.durationMs)
          + ' \u00b7 ' + formatBytes(video.sizeBytes);
        var badges = document.createElement('div');
        badges.className = 'video-badges';
        var showing = video.showing === undefined ? Boolean(video.active) : Boolean(video.showing);
        var scheduledStarts = video.scheduledStarts || [];
        if (showing) {
          var active = document.createElement('span');
          active.className = 'video-badge active';
          active.textContent = 'On mirror';
          badges.appendChild(active);
        }
        scheduledStarts.forEach(function (start) {
          var scheduled = document.createElement('span');
          scheduled.className = 'video-badge';
          scheduled.textContent = 'From ' + formatClockTime(start);
          badges.appendChild(scheduled);
        });
        if (video.previous) {
          var previous = document.createElement('span');
          previous.className = 'video-badge';
          previous.textContent = 'Rollback';
          badges.appendChild(previous);
        }
        details.appendChild(name);
        details.appendChild(metadata);
        details.appendChild(badges);

        var actions = document.createElement('div');
        actions.className = 'video-card-actions';
        var use = document.createElement('button');
        use.type = 'button';
        use.className = 'btn';
        use.textContent = showing ? 'Showing' : (scheduleRunning() ? 'Show now' : 'Use');
        use.disabled = showing;
        use.addEventListener('click', function () {
          activateBackgroundVideo(video.id).catch(function () {});
        });
        var removable = !showing && !video.active && !scheduledStarts.length;
        var remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'btn' + (removable ? ' danger' : '');
        remove.textContent = 'Delete';
        remove.disabled = !removable;
        if (scheduledStarts.length) remove.title = 'Remove this video from the schedule first';
        remove.addEventListener('click', function () {
          var warning = video.previous
            ? ' This also removes the current rollback copy.'
            : '';
          if (!window.confirm('Delete ' + video.name + '?' + warning)) return;
          request('/api/v1/background-videos/' + encodeURIComponent(video.id), {
            method: 'DELETE'
          }).then(function () {
            toast('Background video deleted');
            return refreshBackgroundVideos();
          }).catch(function (error) {
            setMessage('background-video-message', error.message, true);
          });
        });
        actions.appendChild(use);
        actions.appendChild(remove);

        card.appendChild(poster);
        card.appendChild(details);
        card.appendChild(actions);
        list.appendChild(card);
      });

      byId('background-video-rollback').disabled =
        !backgroundVideoCatalog.canRollback;
      var limit = formatBytes(backgroundVideoCatalog.maxLibraryBytes);
      byId('background-video-storage').textContent = videos.length
        ? formatBytes(backgroundVideoCatalog.totalBytes) + ' of ' + limit
          + ' \u00b7 ' + videos.length + (videos.length === 1 ? ' video' : ' videos')
        : 'No videos uploaded \u00b7 ' + limit + ' available';
      renderVideoSchedule();
      if (dashboardLayout) renderLayoutEditor();
      renderHomePreview();
      return catalog;
    });
  }

  function activateBackgroundVideo(id) {
    if (!id) {
      setMessage('background-video-message', 'Choose a video first.', true);
      return Promise.reject(new Error('Choose a video first.'));
    }
    pendingBackgroundVideoId = id;
    setMessage('background-video-message', 'Switching background\u2026');
    return request(
      '/api/v1/background-videos/' + encodeURIComponent(id) + '/activate',
      json('POST', {}))
      .then(function () {
        return Promise.all([
          refreshBackgroundVideos(),
          refreshDashboardLayout(),
          refreshStatus()
        ]);
      })
      .then(function () {
        pendingBackgroundVideoId = '';
        setMessage('background-video-message', '');
        var hold = backgroundVideoCatalog.schedule && backgroundVideoCatalog.schedule.hold;
        toast(hold && hold.videoId === id
          ? 'Showing until ' + formatHoldEnd(hold) + ', then the schedule resumes'
          : 'Background video is on the mirror');
      })
      .catch(function (error) {
        setMessage('background-video-message', error.message, true);
        throw error;
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

  /* ---------- Notes ---------- */

  function refreshNotes() {
    if (!token) return Promise.resolve();
    return request('/api/v1/notes').then(function (result) {
      noteList = result.notes || [];
      notesVersion = typeof result.version === 'number' ? result.version : notesVersion;
      if (typeof result.maxLength === 'number') {
        noteLimit = result.maxLength;
        byId('note-text').maxLength = noteLimit;
        byId('layout-note-text').maxLength = noteLimit;
      }
      if (editingNoteId && !noteList.some(function (note) { return note.id === editingNoteId; })) {
        cancelNoteEdit();
      }
      renderNoteList();
      renderLayoutEditor();
      renderHomePreview();
    });
  }

  function renderNoteList() {
    var list = byId('note-list');
    list.textContent = '';
    byId('note-empty').classList.toggle('hidden', noteList.length > 0);
    noteList.forEach(function (note) {
      var item = document.createElement('li');
      if (note.id === editingNoteId) item.className = 'editing';
      var body = document.createElement('div');
      body.className = 'note-body';
      var text = document.createElement('p');
      text.className = 'note-text';
      text.textContent = note.text;
      var when = document.createElement('small');
      when.textContent = (note.updatedAt !== note.createdAt ? 'Edited ' : 'Posted ') + formatDate(note.updatedAt);
      body.appendChild(text);
      body.appendChild(when);
      var actions = document.createElement('div');
      actions.className = 'icon-row';
      var edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'btn small';
      edit.textContent = 'Edit';
      edit.addEventListener('click', function () { beginNoteEdit(note); });
      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'icon-btn';
      remove.setAttribute('aria-label', 'Delete note');
      remove.innerHTML = '<svg><use href="#i-close"/></svg>';
      remove.addEventListener('click', function () {
        request('/api/v1/notes/' + encodeURIComponent(note.id), { method: 'DELETE' })
          .then(function () {
            toast('Note removed from the mirror');
            return refreshNotes();
          })
          .catch(function (error) { setMessage('note-message', error.message, true); });
      });
      actions.appendChild(edit);
      actions.appendChild(remove);
      item.appendChild(body);
      item.appendChild(actions);
      list.appendChild(item);
    });
  }

  function updateComposerCount() {
    updateNoteCount(byId('note-text'), byId('note-count'));
  }

  function beginNoteEdit(note) {
    editingNoteId = note.id;
    var input = byId('note-text');
    input.value = note.text;
    autosize(input);
    updateComposerCount();
    byId('note-form').querySelector('button[type="submit"]').textContent = 'Save changes';
    byId('note-cancel-edit').classList.remove('hidden');
    renderNoteList();
    input.focus();
    byId('note-form').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function cancelNoteEdit() {
    editingNoteId = '';
    var input = byId('note-text');
    input.value = '';
    autosize(input);
    updateComposerCount();
    byId('note-form').querySelector('button[type="submit"]').textContent = 'Post to mirror';
    byId('note-cancel-edit').classList.add('hidden');
    renderNoteList();
  }

  /* A freshly posted note should be seen. If nothing on the glass shows notes
     yet, turn on the canonical Note widget with the newest note, leaving any
     fixed-text tagline the person wrote alone. */
  function ensureNoteWidgetShowing() {
    if (!savedLayout) return Promise.resolve('');
    var showing = savedLayout.widgets.some(function (widget) {
      return widget.type === 'note' && widget.visible && (widget.source || 'text') !== 'text';
    });
    if (showing) return Promise.resolve('');
    var target = savedLayout.widgets.find(function (widget) { return widget.id === 'note'; });
    if (!target) return Promise.resolve('');
    if (target.visible && (target.source || 'text') === 'text' && target.text) {
      return Promise.resolve('The Note widget shows fixed text. Pick "Newest note" in Display to show notes.');
    }
    var wasClean = layoutText() === layoutBaseline;
    var next = cloneValue(savedLayout);
    var widget = next.widgets.find(function (candidate) { return candidate.id === 'note'; });
    widget.visible = true;
    widget.source = 'latest';
    widget.note = '';
    var working = dashboardLayout && dashboardLayout.widgets.find(function (candidate) { return candidate.id === 'note'; });
    if (working) {
      working.visible = true;
      working.source = 'latest';
      working.note = '';
    }
    return request('/api/v1/dashboard/layout', json('PUT', next)).then(function (saved) {
      savedLayout = cloneValue(saved);
      if (wasClean) {
        dashboardLayout = saved;
        resetLayoutHistory();
      } else {
        commitLayoutChange();
      }
      renderLayoutEditor();
      renderHomePreview();
      return 'The Note widget is now on so you can see it.';
    });
  }

  byId('note-text').addEventListener('input', function () {
    autosize(byId('note-text'));
    updateComposerCount();
  });

  byId('note-cancel-edit').addEventListener('click', cancelNoteEdit);

  byId('note-form').addEventListener('submit', function (event) {
    event.preventDefault();
    var text = byId('note-text').value;
    if (!text.trim()) {
      setMessage('note-message', 'Write something first.', true);
      return;
    }
    var editing = Boolean(editingNoteId);
    var call = editing
      ? request('/api/v1/notes/' + encodeURIComponent(editingNoteId), json('PUT', { text: text }))
      : request('/api/v1/notes', json('POST', { text: text }));
    setMessage('note-message', editing ? 'Saving…' : 'Posting…');
    call
      .then(function () {
        cancelNoteEdit();
        return refreshNotes();
      })
      .then(function () { return editing ? '' : ensureNoteWidgetShowing(); })
      .then(function (extra) {
        var webPage = status && status.dashboardUrl;
        var message = editing ? 'Note updated.' : 'Posted to the mirror.';
        if (webPage) message += ' The mirror is showing a web page; switch to Mirror in Display to see it.';
        else if (extra) message += ' ' + extra;
        setMessage('note-message', message);
        toast(editing ? 'Note updated' : 'Posted to the mirror');
      })
      .catch(function (error) { setMessage('note-message', error.message, true); });
  });

  /* ---------- Board ---------- */

  /* The board is what programs on the network posted (docs/board.md). The
     controls show it so that the people in the house can see what is there,
     tick things off, and clear what they do not want. */
  var boardKindLabels = { note: 'Note', todo: 'To-do', reminder: 'Reminder' };

  function refreshBoard() {
    if (!token) return Promise.resolve();
    return Promise.all([
      request('/api/v1/board'),
      request('/api/v1/board/items?limit=100')
    ]).then(function (results) {
      boardSummary = results[0];
      boardEverything = results[1].items || [];
      boardVersion = typeof results[0].version === 'number' ? results[0].version : boardVersion;
      renderBoardList();
      if (dashboardLayout && !layoutGestureActive) editor.update(dashboardLayout, previewRuntime());
      renderHomePreview();
    });
  }

  function boardItemDetail(item) {
    var parts = [boardKindLabels[item.kind] || item.kind];
    if (item.done) parts.push('done');
    else if (typeof item.due === 'number') parts.push((item.state === 'overdue' ? 'was due ' : 'due ') + formatDate(item.due));
    if (item.source) parts.push('from ' + item.source);
    return parts.join(' \u00b7 ');
  }

  function changeBoard(path, options, done) {
    return request(path, options)
      .then(function () {
        if (done) toast(done);
        setMessage('board-message', '');
        return refreshBoard();
      })
      .catch(function (error) { setMessage('board-message', error.message, true); });
  }

  /* Say so when nothing on the glass shows the board, and offer to fix it. */
  function renderBoardNotice() {
    var shows = Boolean(savedLayout) && savedLayout.widgets.some(function (widget) {
      return widget.type === 'board' && widget.visible;
    });
    var webPage = Boolean(status && status.dashboardUrl);
    byId('board-notice').classList.toggle('hidden', shows || webPage || boardEverything.length === 0);
  }

  function renderBoardList() {
    var list = byId('board-list');
    list.textContent = '';
    byId('board-group').classList.toggle('hidden', boardEverything.length === 0);
    renderBoardNotice();
    boardEverything.forEach(function (entry) {
      var item = document.createElement('li');
      if (entry.done) item.className = 'board-done';
      var body = document.createElement('div');
      body.className = 'note-body';
      var title = document.createElement('p');
      title.className = 'note-text';
      title.textContent = entry.title;
      body.appendChild(title);
      if (entry.body) {
        var detail = document.createElement('p');
        detail.className = 'note-text board-detail';
        detail.textContent = entry.body;
        body.appendChild(detail);
      }
      var facts = document.createElement('small');
      facts.textContent = boardItemDetail(entry);
      body.appendChild(facts);
      var actions = document.createElement('div');
      actions.className = 'icon-row';
      var path = '/api/v1/board/items/' + encodeURIComponent(entry.id);
      if (entry.kind !== 'note') {
        var toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'btn small';
        toggle.textContent = entry.done ? 'Undo' : 'Done';
        toggle.addEventListener('click', function () {
          changeBoard(path, json('PATCH', { done: !entry.done }));
        });
        actions.appendChild(toggle);
      }
      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'icon-btn';
      remove.setAttribute('aria-label', 'Remove from the board');
      remove.innerHTML = '<svg><use href="#i-close"/></svg>';
      remove.addEventListener('click', function () {
        changeBoard(path, { method: 'DELETE' }, 'Removed from the board');
      });
      actions.appendChild(remove);
      item.appendChild(body);
      item.appendChild(actions);
      list.appendChild(item);
    });
  }

  byId('board-clear').addEventListener('click', function () {
    if (!window.confirm('Remove everything from the board?')) return;
    changeBoard('/api/v1/board/items?all=true', { method: 'DELETE' }, 'Board cleared');
  });

  /* Turns the canonical Board widget on where it was last placed. */
  byId('board-show').addEventListener('click', function () {
    if (!savedLayout) return;
    var wasClean = layoutText() === layoutBaseline;
    var next = cloneValue(savedLayout);
    var widget = next.widgets.find(function (candidate) { return candidate.id === 'board'; });
    if (!widget) {
      setMessage('board-message', 'This layout has no Board widget. Reset the layout in Display to get one.', true);
      return;
    }
    widget.visible = true;
    var working = dashboardLayout && dashboardLayout.widgets.find(function (candidate) { return candidate.id === 'board'; });
    if (working) working.visible = true;
    request('/api/v1/dashboard/layout', json('PUT', next))
      .then(function (saved) {
        savedLayout = cloneValue(saved);
        if (wasClean) {
          dashboardLayout = saved;
          resetLayoutHistory();
        } else {
          commitLayoutChange();
        }
        renderLayoutEditor();
        toast('The board is on the mirror');
        return refreshBoard();
      })
      .catch(function (error) { setMessage('board-message', error.message, true); });
  });

  /* ---------- Health ---------- */

  var START_REASONS = {
    update: 'After an update',
    reboot: 'After the mirror restarted',
    killed: 'After Android stopped the app',
    crash: 'After a crash'
  };

  function refreshHealth() {
    if (!token) return Promise.resolve();
    return request('/api/v1/health').then(function (health) {
      renderHealth(health);
      setMessage('health-message', '');
    }).catch(function (error) {
      setMessage('health-message', error.message, true);
    });
  }

  function renderHealth(health) {
    var process = health.process || {};
    var previous = process.previousRun;
    var crashes = health.crashes || {};
    var activity = health.activity || {};
    var dashboard = health.dashboard || {};
    var memory = health.memory || {};
    var storage = health.storage || {};
    var updater = health.otaSupervisor || {};

    byId('health-started').textContent = process.startedAt ? formatDate(process.startedAt) : '—';
    byId('health-previous').textContent = previous
      ? (START_REASONS[previous.end] || 'After an unknown stop')
      : 'First start';

    var crashText = 'None recorded';
    if (crashes.count) {
      var last = crashes.last || {};
      crashText = crashes.count + (crashes.count === 1 ? ' crash' : ' crashes');
      if (last.at) {
        crashText += ', last ' + formatDate(last.at) + ': '
          + String(last.exception || 'unknown error').split('.').pop();
      }
    }
    byId('health-crashes').textContent = crashText;

    var covered = Math.max(Number(activity.pausedForSeconds || 0), Number(activity.unfocusedForSeconds || 0));
    var power = (health.device || {}).power || {};
    var recovery = activity.recovery || {};
    // A covered dashboard is covered whether or not its own display sleeps.
    var dashboardText = power.interactive === false ? 'Display turned off by Android'
      : !activity.showing ? 'Covered by another screen' + (covered >= 60 ? ' for ' + formatUptime(covered) : '')
        : activity.sleeping ? 'Asleep' : 'Showing';
    var broughtBack = Number(recovery.relaunches || 0) + Number(recovery.wakeUps || 0);
    if (broughtBack) {
      dashboardText += ', brought back ' + (broughtBack === 1 ? 'once' : broughtBack + ' times');
    }
    if (dashboard.consoleErrors) {
      dashboardText += ', ' + dashboard.consoleErrors
        + (dashboard.consoleErrors === 1 ? ' script error' : ' script errors');
    }
    byId('health-dashboard').textContent = dashboardText;

    byId('health-memory').textContent = typeof memory.pssKb === 'number'
      ? formatBytes(memory.pssKb * 1024) + ' in use, '
        + formatBytes(Number(memory.systemAvailableKb || 0) * 1024) + ' free'
      : '—';
    byId('health-storage').textContent = typeof storage.dataFreeBytes === 'number'
      ? formatBytes(storage.dataFreeBytes) + ' free of ' + formatBytes(storage.dataTotalBytes)
      : '—';
    // Android restarts the supervisor now and then; a short silence is normal.
    var silentFor = Number(health.now || 0) - Number(updater.unreachableSince || 0);
    byId('health-updater').textContent = !updater.installed ? 'Not installed'
      : updater.listening ? 'Ready' + updaterHoldText(updater)
        : silentFor < 60000 ? 'Not answering right now'
          : 'Not answering since ' + formatDate(updater.unreachableSince);
    byId('health-report').textContent = JSON.stringify(health, null, 2);
  }

  /* Whether Android would end the updater when memory runs short. */
  function updaterHoldText(updater) {
    switch ((updater.hold || {}).state) {
      case 'held':
        return ', kept running by Mirror Home';
      case 'unsupported':
        return '. Version ' + (updater.versionName || '?')
          + ' can be ended when memory runs short; 1.3.0 is kept running.';
      case 'refused':
        return '. It is signed with another key, so it cannot be kept running.';
      default:
        return '';
    }
  }

  /* ---------- Wi-Fi scans ---------- */

  function scanGuardText(guard) {
    if (!guard.supported) return 'Not available: this Android has no such switch.';
    switch (guard.state) {
      case 'applied':
        return 'On. Android looks for networks only when it is not connected.';
      case 'waiting':
        return 'On. Wi\u2011Fi has no network right now, so Android is looking for one.';
      case 'error':
        return 'Android did not take the change' + (guard.detail ? ': ' + guard.detail : '.');
      default:
        return 'Off. Android looks for other networks every few minutes.';
    }
  }

  /* The switch and whether Android took it; the status carries this much every few seconds. */
  function renderScanGuard(guard) {
    if (!guard) return;
    var toggle = byId('scan-guard-enabled');
    if (!scanGuardBusy) {
      toggle.disabled = !guard.supported;
      toggle.checked = Boolean(guard.enabled);
    }
    var element = byId('scan-guard-status');
    element.textContent = scanGuardText(guard);
    element.classList.toggle('error', guard.state === 'error');
    element.classList.toggle('quiet', !guard.enabled);
  }

  /* ---------- Voice ---------- */

  function voiceStatusText(voice) {
    var silent = voiceReport && voiceReport.microphone && voiceReport.microphone.silent;
    switch (voice.state) {
      case 'off':
        return 'Off. Nothing is listening.';
      case 'no-model':
        return 'Waiting for a speech model. Install one to start listening.';
      case 'no-permission':
        return 'Waiting for the microphone permission. Grant it from a computer: '
          + 'tools\\ota.ps1 grant-permission microphone --confirm';
      case 'listening':
        return silent ? 'Listening, but the microphone delivers no sound.' : 'Listening for \u201cMirror\u201d.';
      case 'paused':
        return 'Paused while an update is installed. It starts again by itself.';
      case 'error':
        return voice.detail || 'The recogniser stopped.';
      default:
        return 'Starting\u2026';
    }
  }

  /* The switch and where voice stands; the status carries this much every few seconds. */
  function renderVoiceSummary(voice) {
    if (!voice) return;
    var toggle = byId('voice-enabled');
    if (!voiceBusy) {
      toggle.disabled = false;
      toggle.checked = Boolean(voice.enabled);
    }
    var element = byId('voice-status');
    element.textContent = '';
    if (voice.state === 'listening') {
      var dot = document.createElement('span');
      dot.className = 'live-dot online';
      dot.setAttribute('aria-hidden', 'true');
      element.appendChild(dot);
    }
    element.appendChild(document.createTextNode(voiceStatusText(voice)));
    element.classList.toggle('error', voice.state === 'error');
    element.classList.toggle('quiet', voice.state === 'off');
  }

  function renderVoice(report) {
    voiceReport = report;
    var model = report.model;
    byId('voice-model').textContent = !model
      ? 'Not installed. Choose the model\u2019s zip file here, or run tools\\voice.ps1 install-model on a computer.'
      : model.name ? model.name + ', ' + formatBytes(model.bytes) : 'Installed';
    byId('voice-model-choose').textContent = model ? 'Replace' : 'Install';

    var name = report.wakeWord || 'mirror';
    var spokenName = name.charAt(0).toUpperCase() + name.slice(1);
    var phrases = byId('voice-phrases');
    phrases.textContent = '';
    (report.commands || []).forEach(function (command) {
      var item = document.createElement('li');
      (command.say || []).forEach(function (sentence, index) {
        var rest = sentence.indexOf(name + ' ') === 0 ? sentence.substring(name.length + 1) : sentence;
        if (index) {
          var or = document.createElement('span');
          or.className = 'or';
          or.textContent = ' or ';
          item.appendChild(or);
        }
        item.appendChild(document.createTextNode(
          '\u201c' + (index ? '' : spokenName + ', ') + rest + '\u201d'));
      });
      phrases.appendChild(item);
    });

    var recent = report.recent || [];
    var heard = byId('voice-heard');
    heard.textContent = '';
    recent.slice().reverse().forEach(function (entry) {
      var item = document.createElement('li');
      var time = document.createElement('time');
      time.textContent = new Date(entry.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      var said = document.createElement('span');
      said.className = 'said';
      /* The recogniser knows only the commands' words; anything else arrives as [unk]. */
      said.textContent = '\u201c' + String(entry.heard || '').replace(/\[unk\]/g, '\u2026') + '\u201d';
      var outcome = document.createElement('span');
      outcome.className = 'outcome';
      /* For a command, what the glass showed: "Sleeping", or why nothing changed. */
      outcome.textContent = entry.outcome === 'command' ? (entry.shown || 'Done')
        : entry.outcome === 'wake' ? 'Listened'
          : entry.outcome === 'unsure' ? 'Unsure, ignored'
            : 'Not understood';
      item.appendChild(time);
      item.appendChild(said);
      item.appendChild(outcome);
      heard.appendChild(item);
    });
    var counts = report.counts || {};
    var summary = 'Nothing has been said to the mirror since it last started.';
    if (recent.length) {
      var commands = Number(counts.commands || 0);
      summary = 'Since the mirror last started: ' + commands + (commands === 1 ? ' command' : ' commands') + ' carried out';
      if (counts.unsure) summary += ', ' + counts.unsure + ' ignored because the mirror was unsure';
      if (counts.notUnderstood) summary += ', ' + counts.notUnderstood + ' not understood';
      summary += '.';
    }
    byId('voice-heard-summary').textContent = summary + ' Talk that does not start with its name is not kept.';
    renderVoiceSummary(report);
  }

  function refreshVoice() {
    if (!token) return Promise.resolve();
    return request('/api/v1/voice').then(renderVoice).catch(function (error) {
      setMessage('voice-message', error.message, true);
    });
  }

  /* ---------- Assistant ---------- */

  function assistantStatusText(assistant) {
    switch (assistant.state) {
      case 'off':
        return 'Off. The mirror answers only its own commands.';
      case 'unconfigured':
        return 'Waiting for a companion. Enter its address and key.';
      case 'connecting':
        return 'Looking for the companion\u2026';
      case 'connected':
        return 'Connected' + (assistant.model ? ', answering with ' + assistant.model : '') + '.';
      case 'trouble':
        return assistant.detail || 'The companion reports a problem.';
      case 'unreachable':
        return (assistant.detail || 'The companion does not answer') + '.';
      default:
        return '';
    }
  }

  /* The switch and where the assistant stands; the status carries this much every few seconds. */
  function renderAssistantSummary(assistant) {
    if (!assistant) return;
    /* The status knows the state only; the full report also knows why. */
    if (assistantReport && assistantReport.state === assistant.state) assistant = assistantReport;
    var toggle = byId('assistant-enabled');
    if (!assistantBusy) {
      toggle.disabled = false;
      toggle.checked = Boolean(assistant.enabled);
    }
    var element = byId('assistant-status');
    element.textContent = '';
    if (assistant.state === 'connected') {
      var dot = document.createElement('span');
      dot.className = 'live-dot online';
      dot.setAttribute('aria-hidden', 'true');
      element.appendChild(dot);
    }
    element.appendChild(document.createTextNode(assistantStatusText(assistant)));
    element.classList.toggle('error', assistant.state === 'trouble' || assistant.state === 'unreachable');
    element.classList.toggle('quiet', assistant.state === 'off');
    var canAsk = Boolean(assistant.enabled) && assistant.state !== 'unconfigured' && !assistantAsking;
    byId('assistant-ask').disabled = !canAsk;
    byId('assistant-ask-send').disabled = !canAsk;
    byId('assistant-ask').placeholder = assistantAsking ? 'Asking\u2026'
      : !assistant.enabled ? 'Switch the assistant on first'
        : assistant.state === 'unconfigured' ? 'Set a companion first'
          : 'Ask in your own words';
  }

  function renderAssistant(report) {
    assistantReport = report;
    var address = byId('assistant-address');
    if (document.activeElement !== address && !assistantEdited) address.value = report.address || '';
    byId('assistant-key').placeholder = report.keySet ? 'Saved' : 'Not set';

    var mascot = byId('assistant-mascot');
    if (document.activeElement !== mascot) {
      mascot.textContent = '';
      [{ id: 'none', name: 'None' }].concat(report.mascots || []).forEach(function (each) {
        var option = document.createElement('option');
        option.value = each.id;
        option.textContent = each.name;
        mascot.appendChild(option);
      });
      mascot.value = report.mascot || 'none';
      /* A Mirror Home that knows no characters lists none. */
      mascot.disabled = !report.mascots;
    }

    /* Where the answers stand: how high, and to which side. */
    [['height', 'heights'], ['side', 'sides']].forEach(function (part) {
      var select = byId('assistant-place-' + part[0]);
      if (document.activeElement === select) return;
      var choices = (report.places || {})[part[1]] || [];
      select.textContent = '';
      choices.forEach(function (name) {
        var option = document.createElement('option');
        option.value = name;
        option.textContent = name.charAt(0).toUpperCase() + name.slice(1);
        select.appendChild(option);
      });
      select.value = (report.place || {})[part[0]] || '';
      /* A Mirror Home from before answers could be moved lists no places. */
      select.disabled = choices.length === 0;
    });

    var recent = report.recent || [];
    var list = byId('assistant-recent');
    list.textContent = '';
    recent.slice().reverse().forEach(function (entry) {
      var item = document.createElement('li');
      var time = document.createElement('time');
      time.textContent = new Date(entry.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      var exchange = document.createElement('div');
      exchange.className = 'exchange';
      var said = document.createElement('span');
      said.className = 'said';
      said.textContent = entry.heard ? '\u201c' + entry.heard + '\u201d'
        : entry.error ? 'A request' : 'Nothing that could be made out';
      var reply = document.createElement('span');
      reply.className = 'reply' + (entry.error ? ' error' : '');
      reply.textContent = entry.error ? entry.error
        : entry.ignored ? 'Not meant for the mirror; let pass'
          : (entry.reply || 'Done, without a word')
            + (entry.millis ? ' (' + (entry.millis / 1000).toFixed(1) + ' s)' : '');
      exchange.appendChild(said);
      exchange.appendChild(reply);
      item.appendChild(time);
      item.appendChild(exchange);
      list.appendChild(item);
    });
    var counts = report.counts || {};
    var summary = 'Nothing has been asked since the mirror last started.';
    if (counts.requests) {
      summary = 'Since the mirror last started: ' + counts.requests
        + (counts.requests === 1 ? ' request' : ' requests');
      if (counts.ignored) summary += ', ' + counts.ignored + ' let pass';
      if (counts.failures) summary += ', ' + counts.failures + ' without an answer';
      summary += '.';
    }
    byId('assistant-recent-summary').textContent = summary;
    renderAssistantSummary(report);
  }

  function refreshAssistant() {
    if (!token) return Promise.resolve();
    return request('/api/v1/assistant').then(renderAssistant).catch(function (error) {
      setMessage('assistant-message', error.message, true);
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
          refreshBackgroundVideos(),
          refreshPhotos(),
          refreshBoard(),
          refreshNotes(),
          refreshVoice(),
          refreshAssistant(),
          refreshHealth()
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
    if (name === 'settings') {
      refreshHealth();
      refreshVoice();
      refreshAssistant();
    }
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
    var zone = window.MirrorClock.browserZone();
    var clock = clockFor(zone);
    request('/api/v1/pair', json('POST', {
      code: byId('pair-code').value.trim(),
      name: byId('client-name').value.trim(),
      timeZone: zone,
      utcOffsetMinutes: clock ? clock.utcOffsetMinutes : -new Date().getTimezoneOffset(),
      utcOffsetChanges: clock ? clock.utcOffsetChanges : undefined
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
      applyDashboardUrl('', 'Mirror dashboard is showing');
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
    dashboardLayout.background.fit =
      radioValue('background-fit') || dashboardLayout.background.fit || 'cover';
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
  Array.from(document.querySelectorAll('input[name="background-fit"]')).forEach(function (input) {
    input.addEventListener('change', updateLayoutBackground);
  });
  byId('layout-background-video').addEventListener('change', function () {
    pendingBackgroundVideoId = byId('layout-background-video').value;
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
    autosize(byId('layout-note-text'));
    updateNoteCount(byId('layout-note-text'), byId('layout-note-count'));
    editor.update(dashboardLayout, previewRuntime());
  });
  byId('layout-note-text').addEventListener('change', commitLayoutChange);

  byId('layout-note-source').addEventListener('change', function () {
    var widget = selectedWidget();
    if (!widget || widget.type !== 'note') return;
    var value = byId('layout-note-source').value;
    if (value.indexOf('pinned:') === 0) {
      widget.source = 'pinned';
      widget.note = value.substring('pinned:'.length);
    } else {
      widget.source = value;
      widget.note = '';
    }
    renderLayoutEditor();
    commitLayoutChange();
  });

  Array.from(document.querySelectorAll('input[name="note-size"]')).forEach(function (input) {
    input.addEventListener('change', function () {
      var widget = selectedWidget();
      if (!widget || widget.type !== 'note') return;
      widget.size = input.value;
      renderLayoutEditor();
      commitLayoutChange();
    });
  });

  Array.from(document.querySelectorAll('input[name="note-weight"]')).forEach(function (input) {
    input.addEventListener('change', function () {
      var widget = selectedWidget();
      if (!widget || widget.type !== 'note') return;
      widget.weight = input.value;
      renderLayoutEditor();
      commitLayoutChange();
    });
  });

  byId('layout-board-heading').addEventListener('input', function () {
    var widget = selectedWidget();
    if (!widget || widget.type !== 'board') return;
    widget.text = byId('layout-board-heading').value;
    editor.update(dashboardLayout, previewRuntime());
  });
  byId('layout-board-heading').addEventListener('change', commitLayoutChange);

  byId('layout-board-show').addEventListener('change', function () {
    var widget = selectedWidget();
    if (!widget || widget.type !== 'board') return;
    widget.show = byId('layout-board-show').value;
    renderLayoutEditor();
    commitLayoutChange();
  });

  Array.from(document.querySelectorAll('input[name="board-size"]')).forEach(function (input) {
    input.addEventListener('change', function () {
      var widget = selectedWidget();
      if (!widget || widget.type !== 'board') return;
      widget.size = input.value;
      renderLayoutEditor();
      commitLayoutChange();
    });
  });

  byId('layout-widget-photo').addEventListener('change', function () {
    var widget = selectedWidget();
    if (!widget || widget.type !== 'photo') return;
    widget.photo = byId('layout-widget-photo').value;
    renderLayoutEditor();
    commitLayoutChange();
  });

  Array.from(document.querySelectorAll('input[name="widget-fit"]')).forEach(function (input) {
    input.addEventListener('change', function () {
      var widget = selectedWidget();
      if (!widget || widget.type !== 'photo') return;
      widget.fit = input.value;
      renderLayoutEditor();
      commitLayoutChange();
    });
  });

  /* Tapping a library photo drops it into a frame: the selected frame if one
     is selected, otherwise the canonical Photo widget. */
  function placePhotoInFrame(name) {
    if (!dashboardLayout) return;
    var target = selectedWidget();
    if (!target || target.type !== 'photo') {
      target = dashboardLayout.widgets.find(function (widget) { return widget.id === 'photo'; });
    }
    if (!target) return;
    target.photo = name;
    target.visible = true;
    selectedWidgetId = target.id;
    renderLayoutEditor();
    commitLayoutChange();
    toast('Placed in a frame. Save to show it on the mirror.');
    var editorElement = document.querySelector('.editor');
    if (editorElement) editorElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

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
    var selectedVideoId = byId('layout-background-video').value;
    if (dashboardLayout.background.mode === 'video' && !selectedVideoId) {
      setMessage(
        'layout-message',
        'Upload or choose a background video before saving.',
        true);
      return;
    }
    setMessage('layout-message', 'Saving…');
    var activate = dashboardLayout.background.mode === 'video'
      && selectedVideoId !== showingVideoId()
      ? request(
        '/api/v1/background-videos/' + encodeURIComponent(selectedVideoId) + '/activate',
        json('POST', {}))
      : Promise.resolve();
    activate
      .then(function () {
        pendingBackgroundVideoId = '';
        return request('/api/v1/dashboard/layout', json('PUT', dashboardLayout));
      })
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
        refreshBackgroundVideos().catch(function () {});
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
          var elsewhere = (result.elsewhere || []).map(function (location) { return location.label; });
          setMessage('weather-message', elsewhere.length
            ? 'None was found there. Places of that name: ' + elsewhere.join('; ') + '.'
            : 'No matching places were found. Try the town with its state or country.', true);
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

  /* ---------- Display: background videos ---------- */

  byId('background-video-add').addEventListener('click', function () {
    byId('background-video-file').click();
  });

  byId('background-video-file').addEventListener('change', function () {
    var input = byId('background-video-file');
    var file = input.files[0];
    if (!file) return;
    var maxBytes = Number(backgroundVideoCatalog.maxVideoBytes || 256 * 1024 * 1024);
    if (!/\.mp4$/i.test(file.name)) {
      setMessage('background-video-message', 'Choose an MP4 file.', true);
      input.value = '';
      return;
    }
    if (file.size < 1 || file.size > maxBytes) {
      setMessage(
        'background-video-message',
        'Video must be no larger than ' + formatBytes(maxBytes) + '.',
        true);
      input.value = '';
      return;
    }

    var progress = byId('background-video-progress');
    var bar = progress.querySelector('span');
    var uploadButton = byId('background-video-add');
    uploadButton.disabled = true;
    progress.classList.remove('hidden');
    bar.style.width = '0%';
    setMessage('background-video-message', 'Uploading ' + file.name + '\u2026');

    var xhr = new XMLHttpRequest();
    xhr.open(
      'PUT',
      '/api/v1/background-videos/upload/' + encodeURIComponent(file.name));
    xhr.timeout = 15 * 60 * 1000;
    xhr.setRequestHeader('Authorization', 'Bearer ' + token);
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('Content-Type', 'video/mp4');
    xhr.upload.addEventListener('progress', function (event) {
      if (!event.lengthComputable) return;
      var percent = Math.min(100, Math.round(event.loaded * 100 / event.total));
      bar.style.width = percent + '%';
      setMessage(
        'background-video-message',
        'Uploading ' + file.name + '\u2026 ' + percent + '%');
    });
    xhr.addEventListener('load', function () {
      var body = {};
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : {};
      } catch (error) {
        body = {};
      }
      if (xhr.status < 200 || xhr.status >= 300 || !body.video || !body.video.id) {
        progress.classList.add('hidden');
        uploadButton.disabled = false;
        input.value = '';
        setMessage(
          'background-video-message',
          body.error || 'Upload failed (' + xhr.status + ')',
          true);
        return;
      }
      bar.style.width = '100%';
      setMessage('background-video-message', 'Validating and switching background\u2026');
      activateBackgroundVideo(body.video.id)
        .then(function () {
          progress.classList.add('hidden');
          uploadButton.disabled = false;
          input.value = '';
        })
        .catch(function () {
          progress.classList.add('hidden');
          uploadButton.disabled = false;
          input.value = '';
          refreshBackgroundVideos().catch(function () {});
        });
    });
    xhr.addEventListener('error', function () {
      progress.classList.add('hidden');
      uploadButton.disabled = false;
      input.value = '';
      setMessage('background-video-message', 'Upload connection failed.', true);
    });
    xhr.addEventListener('timeout', function () {
      progress.classList.add('hidden');
      uploadButton.disabled = false;
      input.value = '';
      setMessage('background-video-message', 'Upload timed out.', true);
    });
    xhr.addEventListener('abort', function () {
      progress.classList.add('hidden');
      uploadButton.disabled = false;
      input.value = '';
      setMessage('background-video-message', 'Upload cancelled.', true);
    });
    xhr.send(file);
  });

  byId('background-video-rollback').addEventListener('click', function () {
    var button = byId('background-video-rollback');
    button.disabled = true;
    setMessage('background-video-message', 'Restoring previous background\u2026');
    request('/api/v1/background-videos/rollback', json('POST', {}))
      .then(function () {
        pendingBackgroundVideoId = '';
        return Promise.all([
          refreshBackgroundVideos(),
          refreshDashboardLayout(),
          refreshStatus()
        ]);
      })
      .then(function () {
        setMessage('background-video-message', '');
        toast('Previous background restored');
      })
      .catch(function (error) {
        button.disabled = !backgroundVideoCatalog.canRollback;
        setMessage('background-video-message', error.message, true);
      });
  });

  var SUGGESTED_SCHEDULE_TIMES = ['06:00', '19:00', '12:00', '22:00', '09:00', '15:00', '00:00', '03:00'];

  byId('video-schedule-enabled').addEventListener('change', function () {
    var draft = currentScheduleDraft();
    draft.enabled = byId('video-schedule-enabled').checked;
    if (draft.enabled && !draft.slots.length) {
      var videos = backgroundVideoCatalog.videos || [];
      var morning = showingVideoId() || (videos[0] ? videos[0].id : '');
      var others = videos.filter(function (video) { return video.id !== morning; });
      draft.slots = [
        { start: '06:00', videoId: morning },
        { start: '19:00', videoId: others.length ? others[0].id : morning }
      ];
    }
    videoScheduleDirty = true;
    renderVideoSchedule();
  });

  byId('video-schedule-add').addEventListener('click', function () {
    var draft = currentScheduleDraft();
    var used = draft.slots.map(function (slot) { return slot.start; });
    var start = SUGGESTED_SCHEDULE_TIMES.filter(function (time) {
      return used.indexOf(time) < 0;
    })[0] || '';
    draft.slots.push({ start: start, videoId: showingVideoId() });
    videoScheduleDirty = true;
    renderVideoSchedule();
  });

  byId('video-schedule-save').addEventListener('click', function () {
    var draft = currentScheduleDraft();
    var seen = {};
    for (var index = 0; index < draft.slots.length; index++) {
      var slot = draft.slots[index];
      if (!/^\d{2}:\d{2}$/.test(slot.start || '')) {
        setMessage('video-schedule-message', 'Enter a start time for every row.', true);
        return;
      }
      if (!findBackgroundVideo(slot.videoId)) {
        setMessage('video-schedule-message', 'Choose a video for every time.', true);
        return;
      }
      if (seen[slot.start]) {
        setMessage('video-schedule-message', 'Each time must be different.', true);
        return;
      }
      seen[slot.start] = true;
    }
    if (draft.enabled && !draft.slots.length) {
      setMessage('video-schedule-message', 'Add at least one time first.', true);
      return;
    }
    var button = byId('video-schedule-save');
    button.disabled = true;
    setMessage('video-schedule-message', 'Saving schedule\u2026');
    request('/api/v1/background-videos/schedule', json('PUT', {
      enabled: draft.enabled,
      slots: draft.slots
    }))
      .then(function () {
        videoScheduleDirty = false;
        videoScheduleDraft = null;
        return Promise.all([
          refreshBackgroundVideos(),
          refreshDashboardLayout(),
          refreshStatus()
        ]);
      })
      .then(function () {
        setMessage('video-schedule-message', '');
        toast(scheduleRunning() ? 'Video schedule is on' : 'Video schedule is off');
      })
      .catch(function (error) {
        setMessage('video-schedule-message', error.message, true);
      })
      .then(function () {
        button.disabled = false;
      });
  });

  byId('video-schedule-resume').addEventListener('click', function () {
    request('/api/v1/background-videos/schedule/resume', json('POST', {}))
      .then(function () {
        return Promise.all([refreshBackgroundVideos(), refreshStatus()]);
      })
      .then(function () { toast('The schedule is back on the mirror'); })
      .catch(function (error) {
        setMessage('video-schedule-message', error.message, true);
      });
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
    var zone = byId('time-zone').value.trim();
    var clock = clockFor(zone);
    if (!clock) {
      setMessage('clock-message',
        'This browser does not recognize "' + zone + '". Use an IANA name such as America/Los_Angeles.', true);
      return;
    }
    request('/api/v1/preferences', json('PUT', {
      timeZone: zone,
      utcOffsetMinutes: clock.utcOffsetMinutes,
      utcOffsetChanges: clock.utcOffsetChanges,
      clock24Hour: byId('clock-24-hour').checked
    })).then(function (preferences) {
      setMessage('clock-message', '');
      toast('Clock saved');
      return refreshStatus().then(function () { renderClockHint(preferences); });
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

  var pairWindowTimer = null;
  byId('open-pair-window').addEventListener('click', function () {
    request('/api/v1/pair/window', json('POST', {})).then(function (result) {
      var code = String(result.code || '');
      var host = status && (status.address || (status.wifi && status.wifi.ipAddress));
      var address = host ? 'http://' + host + ':8787' : 'the mirror\u2019s address';
      var until = new Date(Number(result.expiresAt))
        .toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      byId('pair-window-code').textContent = code.slice(0, 3) + ' ' + code.slice(3);
      byId('pair-window-detail').textContent = 'Open ' + address
        + ' on the new device and enter this code. It works once, until ' + until + '.';
      byId('pair-window').classList.remove('hidden');
      setMessage('access-message', '');
      if (pairWindowTimer) window.clearTimeout(pairWindowTimer);
      pairWindowTimer = window.setTimeout(function () {
        byId('pair-window').classList.add('hidden');
        refreshClients().catch(function () {});
      }, Math.max(1, Number(result.expiresInSeconds || 0)) * 1000);
    }).catch(function (error) { setMessage('access-message', error.message, true); });
  });

  byId('forget-this-device').addEventListener('click', function () {
    if (!window.confirm('Forget this browser? You will need the pairing code to connect again.')) return;
    request('/api/v1/pair/revoke', json('POST', {}))
      .catch(function () { return null; })
      .then(forgetLocalCredential);
  });

  /* ---------- Voice ---------- */

  byId('scan-guard-enabled').addEventListener('change', function () {
    var toggle = byId('scan-guard-enabled');
    var wanted = toggle.checked;
    scanGuardBusy = true;
    toggle.disabled = true;
    setMessage('scan-guard-message', '');
    request('/api/v1/wifi/scan-guard', json('PUT', { enabled: wanted })).then(function (guard) {
      scanGuardBusy = false;
      renderScanGuard(guard);
    }).catch(function (error) {
      scanGuardBusy = false;
      toggle.disabled = false;
      toggle.checked = !wanted;
      setMessage('scan-guard-message', error.message, true);
    });
  });

  byId('voice-enabled').addEventListener('change', function () {
    var toggle = byId('voice-enabled');
    var wanted = toggle.checked;
    voiceBusy = true;
    toggle.disabled = true;
    setMessage('voice-message', '');
    request('/api/v1/voice', json('PUT', { enabled: wanted })).then(function (report) {
      voiceBusy = false;
      renderVoice(report);
      /* The recogniser takes some seconds to load its model. */
      if (wanted) window.setTimeout(refreshVoice, 6000);
    }).catch(function (error) {
      voiceBusy = false;
      toggle.disabled = false;
      toggle.checked = !wanted;
      setMessage('voice-message', error.message, true);
    });
  });

  byId('voice-model-choose').addEventListener('click', function () {
    byId('voice-model-file').click();
  });

  byId('voice-model-file').addEventListener('change', function () {
    var input = byId('voice-model-file');
    var file = input.files[0];
    if (!file) return;
    var maxBytes = Number((voiceReport && voiceReport.maxModelBytes) || 96 * 1024 * 1024);
    if (!/\.zip$/i.test(file.name) || file.size < 1 || file.size > maxBytes) {
      setMessage(
        'voice-message',
        'Choose a speech model\u2019s zip file, no larger than ' + formatBytes(maxBytes) + '.',
        true);
      input.value = '';
      return;
    }

    var progress = byId('voice-model-progress');
    var bar = progress.querySelector('span');
    var button = byId('voice-model-choose');
    button.disabled = true;
    progress.classList.remove('hidden');
    bar.style.width = '0%';
    setMessage('voice-message', 'Sending ' + file.name + '\u2026');

    function finish(message, error) {
      progress.classList.add('hidden');
      button.disabled = false;
      input.value = '';
      setMessage('voice-message', message, error);
    }

    var xhr = new XMLHttpRequest();
    xhr.open('PUT', '/api/v1/voice/model');
    xhr.timeout = 15 * 60 * 1000;
    xhr.setRequestHeader('Authorization', 'Bearer ' + token);
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('Content-Type', 'application/zip');
    xhr.upload.addEventListener('progress', function (event) {
      if (!event.lengthComputable) return;
      var percent = Math.min(100, Math.round(event.loaded * 100 / event.total));
      bar.style.width = percent + '%';
      setMessage('voice-message', percent < 100
        ? 'Sending ' + file.name + '\u2026 ' + percent + '%'
        : 'The mirror is unpacking the model\u2026');
    });
    xhr.addEventListener('load', function () {
      var body = {};
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : {};
      } catch (error) {
        body = {};
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        finish(body.error || 'The upload failed (' + xhr.status + ').', true);
        return;
      }
      finish('');
      renderVoice(body);
      toast('Speech model installed');
    });
    xhr.addEventListener('error', function () { finish('The upload did not reach the mirror.', true); });
    xhr.addEventListener('timeout', function () { finish('The upload took too long.', true); });
    xhr.send(file);
  });

  /* ---------- Assistant ---------- */

  byId('assistant-enabled').addEventListener('change', function () {
    var toggle = byId('assistant-enabled');
    var wanted = toggle.checked;
    assistantBusy = true;
    toggle.disabled = true;
    setMessage('assistant-message', '');
    request('/api/v1/assistant', json('PUT', { enabled: wanted })).then(function (report) {
      assistantBusy = false;
      renderAssistant(report);
      /* The mirror asks the companion how it is right away; the answer takes a moment. */
      if (wanted) window.setTimeout(refreshAssistant, 2500);
    }).catch(function (error) {
      assistantBusy = false;
      toggle.disabled = false;
      toggle.checked = !wanted;
      setMessage('assistant-message', error.message, true);
    });
  });

  byId('assistant-mascot').addEventListener('change', function () {
    var select = byId('assistant-mascot');
    var before = assistantReport ? assistantReport.mascot : 'none';
    select.disabled = true;
    setMessage('assistant-message', '');
    request('/api/v1/assistant', json('PUT', { mascot: select.value })).then(function (report) {
      select.blur();
      renderAssistant(report);
    }).catch(function (error) {
      select.disabled = false;
      select.value = before;
      setMessage('assistant-message', error.message, true);
    });
  });

  ['height', 'side'].forEach(function (part) {
    byId('assistant-place-' + part).addEventListener('change', function () {
      var select = byId('assistant-place-' + part);
      var before = assistantReport && assistantReport.place ? assistantReport.place[part] : '';
      var place = {};
      place[part] = select.value;
      select.disabled = true;
      setMessage('assistant-message', '');
      request('/api/v1/assistant', json('PUT', { place: place })).then(function (report) {
        select.blur();
        renderAssistant(report);
      }).catch(function (error) {
        select.disabled = false;
        select.value = before;
        setMessage('assistant-message', error.message, true);
      });
    });
  });

  byId('assistant-address').addEventListener('input', function () { assistantEdited = true; });

  byId('assistant-form').addEventListener('submit', function (event) {
    event.preventDefault();
    var body = { address: byId('assistant-address').value.trim() };
    var key = byId('assistant-key').value.trim();
    if (key) body.key = key;
    var button = byId('assistant-save');
    button.disabled = true;
    setMessage('assistant-message', '');
    request('/api/v1/assistant', json('PUT', body)).then(function (report) {
      button.disabled = false;
      assistantEdited = false;
      byId('assistant-key').value = '';
      renderAssistant(report);
      toast('Companion saved');
      window.setTimeout(refreshAssistant, 2500);
    }).catch(function (error) {
      button.disabled = false;
      setMessage('assistant-message', error.message, true);
    });
  });

  byId('assistant-ask-form').addEventListener('submit', function (event) {
    event.preventDefault();
    var input = byId('assistant-ask');
    var text = input.value.trim();
    if (!text || assistantAsking) return;
    assistantAsking = true;
    renderAssistantSummary(assistantReport);
    setMessage('assistant-answer', 'Asking\u2026');
    request('/api/v1/assistant/ask', json('POST', { text: text })).then(function (answer) {
      input.value = '';
      setMessage('assistant-answer', answer.ignored ? 'The assistant let that pass.'
        : answer.reply ? '\u201c' + answer.reply + '\u201d'
          : 'Done, without a word.');
    }).catch(function (error) {
      setMessage('assistant-answer', error.message, true);
    }).then(function () {
      assistantAsking = false;
      return refreshAssistant();
    }).then(function () {
      renderAssistantSummary(assistantReport);
      byId('assistant-ask').focus();
    });
  });

  /* ---------- Boot ---------- */

  if (!byId('time-zone').value) {
    byId('time-zone').value = window.MirrorClock.browserZone();
  }
  showPairedState(Boolean(token));
  refreshAll();
  alignedTick();
  window.setInterval(function () {
    /* Unpaired, this re-reads whether the mirror is showing a code yet. */
    if (document.hidden) return;
    refreshStatus().catch(function () {});
    /* What the mirror heard changes while Settings is open. */
    if (token && byId('settings').classList.contains('active')) {
      refreshVoice();
      refreshAssistant();
    }
  }, 10000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) refreshStatus().catch(function () {});
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
