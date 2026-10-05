/* Shared mirror renderer.
   One source of truth for how the Mirror looks: used by the Mirror's own
   WebView (Chromium 44: strict ES5 only), the layout editor, and the live
   preview in the control application. */
(function (global) {
  'use strict';

  var TEXT_TYPES = { clock: true, date: true, name: true, note: true, board: true };
  var NOTE_WEIGHTS = { thin: true, light: true, regular: true, medium: true };
  /* Fixed note sizes scale with the shorter stage edge so they read the same
     on the glass, the editor canvas, and the Home preview. */
  var NOTE_SIZES = { small: 0.022, medium: 0.032, large: 0.046 };
  /* The board lists what programs posted (see BoardItems.java). Its type is a
     fixed size, like a note's, and what does not fit waits on a later page. */
  var BOARD_SIZES = { small: 0.022, medium: 0.028, large: 0.036 };
  var BOARD_PAGE_MS = 10000;
  var BOARD_MARKS = {
    note: '<circle class="mr-fill" cx="12" cy="12" r="2.6"/>',
    todo: '<circle cx="12" cy="12" r="7.5"/>',
    reminder: '<circle cx="12" cy="12" r="7.5"/><path d="M12 7.8V12l2.8 1.8"/>',
    done: '<circle cx="12" cy="12" r="7.5"/><path d="M8.4 12.3l2.5 2.5 4.8-5.2"/>'
  };
  /* Shown in the layout editor while the board is empty, so the widget can
     be placed and sized against something. dueIn is minutes from now. */
  var SAMPLE_BOARD = [
    { id: 'sample-1', kind: 'reminder', title: 'Leave for the dentist', dueIn: 25, priority: 'high' },
    { id: 'sample-2', kind: 'todo', title: 'Water the plants', body: 'Not the cactus' },
    { id: 'sample-3', kind: 'note', title: 'Dinner is in the oven' },
    { id: 'sample-4', kind: 'todo', title: 'Take out the bins', done: true }
  ];
  var WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
    'August', 'September', 'October', 'November', 'December'];
  var SAMPLE_WEATHER = {
    current: { temperature: 72, weatherCode: 2, daylight: true, condition: 'Partly cloudy' },
    daily: [{ high: 78, low: 61, precipitationProbability: 10 }],
    hourly: [
      { time: 0, temperature: 74, weatherCode: 2, precipitationProbability: 0 },
      { time: 3600000, temperature: 75, weatherCode: 1, precipitationProbability: 0 },
      { time: 7200000, temperature: 73, weatherCode: 3, precipitationProbability: 20 },
      { time: 10800000, temperature: 70, weatherCode: 61, precipitationProbability: 55 },
      { time: 14400000, temperature: 66, weatherCode: 61, precipitationProbability: 40 },
      { time: 18000000, temperature: 63, weatherCode: 2, precipitationProbability: 10 }
    ]
  };

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function pad(value) {
    return value < 10 ? '0' + value : String(value);
  }

  /* The Mirror's UTC offset at a moment. The runtime carries the offset in
     force and the next change, so a daylight-saving switch lands on time
     even between runtime refreshes. */
  function offsetAt(time, runtime) {
    if (!runtime) return 0;
    var next = runtime.nextUtcOffsetChange;
    if (next && typeof next.at === 'number' && time >= next.at) {
      return Number(next.utcOffsetMinutes || 0);
    }
    return Number(runtime.utcOffsetMinutes || 0);
  }

  function localDate(now, runtime) {
    return new Date(now.getTime() + offsetAt(now.getTime(), runtime) * 60000);
  }

  function timeParts(date, clock24Hour) {
    var hours = date.getUTCHours();
    var minutes = pad(date.getUTCMinutes());
    if (clock24Hour) {
      return { digits: pad(hours) + ':' + minutes, meridiem: '' };
    }
    return {
      digits: (hours % 12 || 12) + ':' + minutes,
      meridiem: hours >= 12 ? 'PM' : 'AM'
    };
  }

  function hourLabel(date, clock24Hour) {
    var hours = date.getUTCHours();
    if (clock24Hour) return pad(hours);
    return (hours % 12 || 12) + ' ' + (hours >= 12 ? 'PM' : 'AM');
  }

  function formatDate(date) {
    return WEEKDAYS[date.getUTCDay()] + ', ' + MONTHS[date.getUTCMonth()] + ' ' + date.getUTCDate();
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

  function temperature(value) {
    return typeof value === 'number' ? Math.round(value) + '\u00b0' : '--';
  }

  /* Stroke icons on a 24-unit grid; WMO weather codes map onto them. */
  var CLOUD = 'M7 18.5h10a3.5 3.5 0 0 0 .5-6.96A5.5 5.5 0 0 0 7 12.5a3 3 0 0 0 0 6z';
  var CLOUD_HIGH = 'M7 15h10a3.5 3.5 0 0 0 .5-6.96A5.5 5.5 0 0 0 7 9a3 3 0 0 0 0 6z';
  var CLOUD_SMALL = 'M10.5 20h7.8a3 3 0 0 0 .4-5.97A4.6 4.6 0 0 0 10.5 15a2.5 2.5 0 0 0 0 5z';
  var SUN_RAYS = 'M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.55 1.55' +
    'M17.15 17.15l1.55 1.55M5.3 18.7l1.55-1.55M17.15 6.85l1.55-1.55';
  var SMALL_SUN_RAYS = 'M8 2.4v1.6M2.4 8h1.6M4.05 4.05l1.1 1.1M4.05 11.95l1.1-1.1M11.95 4.05l-1.1 1.1';
  var ICONS = {
    sun: '<circle cx="12" cy="12" r="4.2"/><path d="' + SUN_RAYS + '"/>',
    moon: '<path d="M12 3a6.4 6.4 0 0 0 9 9 9 9 0 1 1-9-9z"/>',
    'sun-cloud': '<circle cx="8" cy="8" r="3.1"/><path d="' + SMALL_SUN_RAYS + '"/>' +
      '<path d="' + CLOUD_SMALL + '"/>',
    'moon-cloud': '<path d="M7.5 2.2a4.3 4.3 0 0 0 5.5 5.5 6 6 0 1 1-5.5-5.5z"/><path d="' + CLOUD_SMALL + '"/>',
    cloud: '<path d="' + CLOUD + '"/>',
    fog: '<path d="' + CLOUD_HIGH + '"/><path d="M5.5 18.5h13M7.5 21.5h9"/>',
    drizzle: '<path d="' + CLOUD_HIGH + '"/><path d="M9 18.6l-.4 1.3M12.5 18.6l-.4 1.3M16 18.6l-.4 1.3"/>',
    rain: '<path d="' + CLOUD_HIGH + '"/><path d="M9.4 17.6l-1.1 3.4M12.9 17.6l-1.1 3.4M16.4 17.6l-1.1 3.4"/>',
    snow: '<path d="' + CLOUD_HIGH + '"/><circle class="mr-fill" cx="8.6" cy="18.6" r=".9"/>' +
      '<circle class="mr-fill" cx="12" cy="20.8" r=".9"/><circle class="mr-fill" cx="15.4" cy="18.6" r=".9"/>',
    thunder: '<path d="' + CLOUD_HIGH + '"/><path d="M12.8 15.4l-2.2 3.7h3.2l-2.2 3.7"/>'
  };

  function iconName(code, daylight) {
    var night = daylight === false;
    if (code === 0 || code === 1) return night ? 'moon' : 'sun';
    if (code === 2) return night ? 'moon-cloud' : 'sun-cloud';
    if (code === 3) return 'cloud';
    if (code === 45 || code === 48) return 'fog';
    if (code >= 51 && code <= 57) return 'drizzle';
    if (code >= 61 && code <= 67) return 'rain';
    if (code >= 71 && code <= 77) return 'snow';
    if (code >= 80 && code <= 82) return 'rain';
    if (code >= 85 && code <= 86) return 'snow';
    if (code >= 95 && code <= 99) return 'thunder';
    return 'cloud';
  }

  function iconSvg(code, daylight, className) {
    return '<span class="' + className + '"><svg class="mr-icon-svg" viewBox="0 0 24 24" ' +
      'aria-hidden="true">' + ICONS[iconName(code, daylight)] + '</svg></span>';
  }

  function weatherData(runtime, editing) {
    var weather = runtime && runtime.weather ? runtime.weather : {};
    if (weather.data && weather.data.current) return weather.data;
    return editing ? SAMPLE_WEATHER : null;
  }

  function create(container, options) {
    var settings = options || {};
    var editing = Boolean(settings.editing);
    var nativeVideo = Boolean(settings.nativeVideo);
    var selectedId = '';
    var layout = null;
    var runtime = null;
    var now = new Date();
    var nodes = {};
    var backgroundKey = '';
    var photoToken = 0;
    var library = { names: [], fetchedAt: 0, pending: false };
    var slideIndex = 0;
    var slideTimer = null;
    var SLIDE_INTERVAL_MS = 20000;
    var LIBRARY_TTL_MS = 60000;
    var pixelRatio = window.devicePixelRatio || 1;
    /* Pick the smallest server-side variant that still fills the box crisply. */
    var resolvePhoto = settings.resolvePhoto || function (name, done, box) {
      var edge = box ? Math.max(box.width, box.height) * pixelRatio : Infinity;
      var variant = edge <= 320 ? '/thumbnail' : '/display';
      done('/photos/' + encodeURIComponent(name) + variant);
    };
    var resolveVideoPoster = settings.resolveVideoPoster || function (id, done) {
      done('');
    };
    var listPhotos = settings.listPhotos || function (done) {
      fetch('/api/v1/photos/slideshow', { cache: 'no-store' }).then(function (response) {
        return response.json();
      }).then(function (result) {
        done((result.photos || []).map(function (photo) { return photo.name; }));
      }).then(null, function () { done(null); });
    };

    /* The glass moves; the editor does not. A widget that fades or glides
       under someone's finger while they place it would only be in the way. */
    container.className += (container.className ? ' ' : '') + 'mr-stage' +
      (editing ? ' mr-editing' : ' mr-live');
    /* Whether the glass has been drawn once: what is there at the start
       arrives with the stage, and only what comes later arrives by itself. */
    var drawn = false;
    var LEAVE_MS = 650;
    var DISSOLVE_MS = 320;
    var photoLayer = document.createElement('div');
    photoLayer.className = 'mr-layer mr-backdrop';
    var shadeLayer = document.createElement('div');
    shadeLayer.className = 'mr-layer mr-shade';
    var widgetLayer = document.createElement('div');
    widgetLayer.className = 'mr-layer mr-widgets';
    /* Moments lie over the widgets: what is on the glass for a while. */
    var momentLayer = document.createElement('div');
    momentLayer.className = 'mr-layer mr-moments';
    var momentNodes = {};
    /* Moments that had to make room for a newer one, as id and the time each was made. */
    var displaced = {};
    var dropMoment = settings.dropMoment || function () {};
    container.appendChild(photoLayer);
    container.appendChild(shadeLayer);
    container.appendChild(widgetLayer);
    container.appendChild(momentLayer);

    function stageSize() {
      return { width: container.clientWidth || 1, height: container.clientHeight || 1 };
    }

    function boxSize(widget) {
      var stage = stageSize();
      return {
        width: stage.width * widget.w / 1000,
        height: stage.height * widget.h / 1000
      };
    }

    function baseFontSize(widget, box) {
      switch (widget.type) {
        case 'clock': return box.height * 0.82;
        case 'date': return box.height * 0.66;
        case 'name': return box.height * 0.62;
        case 'note': return noteFontSize(widget, box);
        case 'board': return boardFontSize(widget);
        case 'weather': return box.height / 1.55;
        case 'forecast': return forecastFontSize(box);
        default: return box.height / 1.72;
      }
    }

    function noteFontSize(widget, box) {
      var scale = NOTE_SIZES[widget.size];
      if (!scale) return Math.min(box.height * 0.3, box.width * 0.075);
      var stage = stageSize();
      return Math.min(stage.width, stage.height) * scale;
    }

    /* Fixed-size notes and the board keep their size and clip; everything
       else shrinks to fit. */
    function shrinksToFit(widget) {
      if (widget.type === 'board') return false;
      return widget.type !== 'note' || !NOTE_SIZES[widget.size];
    }

    function forecastColumns(box) {
      var tall = box.height / 3.05;
      var count = 6;
      while (count > 2 && box.width / (count * 2.3) < tall * 0.72) count -= 1;
      return count;
    }

    function forecastFontSize(box) {
      return Math.min(box.height / 3.05, box.width / (forecastColumns(box) * 2.3));
    }

    function fitContent(node, widget, probeHtml) {
      var box = boxSize(widget);
      var base = baseFontSize(widget, box);
      var inner = node.inner;
      var element = node.element;
      element.style.fontSize = base + 'px';
      if (!shrinksToFit(widget)) return;
      var realHtml = inner.innerHTML;
      if (probeHtml) inner.innerHTML = probeHtml;
      /* Widgets may nominate a child (.mr-fit) whose width governs sizing. */
      var gauge = inner.querySelector('.mr-fit') || inner;
      var width = Math.max(gauge.offsetWidth, gauge.scrollWidth);
      var height = Math.max(inner.offsetHeight, inner.scrollHeight);
      if (width > box.width || height > box.height) {
        var ratio = Math.min(box.width / Math.max(1, width), box.height / Math.max(1, height));
        element.style.fontSize = Math.max(6, Math.floor(base * ratio * 0.985)) + 'px';
      }
      if (probeHtml) inner.innerHTML = realHtml;
    }

    /* Keep the weather detail on one line: drop the least valuable segments
       (rain chance, then the condition the icon already conveys), then shrink. */
    function trimDetail(node, widget) {
      var detail = node.inner.querySelector('.mr-detail');
      if (!detail) return;
      var limit = boxSize(widget).width;
      var segments = detail.querySelectorAll('.mr-seg');
      while (detail.scrollWidth > limit && segments.length > 1) {
        var victim = segments[0];
        for (var index = 1; index < segments.length; index++) {
          if (Number(segments[index].getAttribute('data-drop')) > Number(victim.getAttribute('data-drop'))) {
            victim = segments[index];
          }
        }
        var separator = victim.previousSibling && victim.previousSibling.className === 'mr-sep'
          ? victim.previousSibling
          : victim.nextSibling;
        if (separator && separator.className === 'mr-sep') detail.removeChild(separator);
        detail.removeChild(victim);
        segments = detail.querySelectorAll('.mr-seg');
      }
      if (detail.scrollWidth > limit) {
        detail.style.fontSize = (0.29 * limit / detail.scrollWidth * 0.98).toFixed(3) + 'em';
      }
    }

    function clockHtml(local) {
      var parts = timeParts(local, runtime && runtime.clock24Hour);
      return '<span class="mr-line"><span class="mr-digits mr-thin">' + parts.digits + '</span>' +
        (parts.meridiem ? '<span class="mr-meridiem mr-light">' + parts.meridiem + '</span>' : '') +
        '</span>';
    }

    function clockProbe() {
      var twentyFour = runtime && runtime.clock24Hour;
      return '<span class="mr-line"><span class="mr-digits mr-thin">' + (twentyFour ? '00:00' : '10:00') +
        '</span>' + (twentyFour ? '' : '<span class="mr-meridiem mr-light">PM</span>') + '</span>';
    }

    function metric(label, value, quiet) {
      return '<span class="mr-label mr-medium">' + label + '</span><span class="mr-value mr-light' +
        (quiet ? ' mr-quiet' : '') + '">' + value + '</span>';
    }

    function scheduleValue(automation) {
      if (!automation) return { text: 'Off', quiet: true };
      if (automation.sleeping) {
        return {
          text: automation.sleepReason === 'inactivity' ? 'Waiting for motion' : 'Sleeping',
          quiet: false
        };
      }
      if (!automation.enabled || !automation.sleepTime) return { text: 'Off', quiet: true };
      var pieces = String(automation.sleepTime).split(':');
      var date = new Date(Date.UTC(2000, 0, 1, Number(pieces[0]) || 0, Number(pieces[1]) || 0));
      var parts = timeParts(date, runtime && runtime.clock24Hour);
      return { text: 'Sleeps ' + parts.digits + (parts.meridiem ? ' ' + parts.meridiem : ''), quiet: false };
    }

    function motionValue(automation) {
      var motion = automation && automation.motion ? automation.motion : {};
      if (!automation || !automation.motionEnabled) return { text: 'Off', quiet: true };
      if (!motion.monitoring) return { text: 'Unavailable', quiet: true };
      var recent = typeof motion.lastMotionAgeSeconds === 'number' && motion.lastMotionAgeSeconds < 30;
      return { text: recent ? 'Movement' : 'Watching', quiet: false };
    }

    function weatherHtml() {
      var data = weatherData(runtime, editing);
      if (!data) return '';
      var current = data.current;
      var today = data.daily && data.daily[0];
      var stale = runtime && runtime.weather && runtime.weather.stale;
      var details = ['<span class="mr-seg" data-drop="2">' + escapeHtml(current.condition || '') + '</span>'];
      if (today) {
        details.push('<span class="mr-seg" data-drop="1">' + temperature(today.high) + ' / ' + temperature(today.low) + '</span>');
        if (Number(today.precipitationProbability || 0) >= 20) {
          details.push('<span class="mr-seg" data-drop="3">Rain ' + Number(today.precipitationProbability) + '%</span>');
        }
      }
      return '<span class="mr-now mr-fit">' +
        iconSvg(Number(current.weatherCode), current.daylight !== false, 'mr-icon') +
        '<span class="mr-temp mr-thin">' + temperature(current.temperature) + '</span></span>' +
        '<span class="mr-detail mr-regular' + (stale ? ' mr-stale' : '') + '">' +
        details.join('<span class="mr-sep">&middot;</span>') + '</span>';
    }

    function forecastHtml(widget) {
      var data = weatherData(runtime, editing);
      if (!data || !data.hourly || !data.hourly.length) return '';
      var box = boxSize(widget);
      var hours = data.hourly.slice(0, Math.min(data.hourly.length, forecastColumns(box)));
      var clock24Hour = runtime && runtime.clock24Hour;
      return '<span class="mr-hours">' + hours.map(function (hour) {
        var date = new Date(Number(hour.time) + offsetAt(Number(hour.time), runtime) * 60000);
        var rain = Number(hour.precipitationProbability || 0);
        return '<span class="mr-hour">' +
          '<span class="mr-hour-label mr-medium">' + escapeHtml(hourLabel(date, clock24Hour)) + '</span>' +
          iconSvg(Number(hour.weatherCode), true, 'mr-hour-icon') +
          '<span class="mr-hour-temp mr-light">' + temperature(hour.temperature) + '</span>' +
          '<span class="mr-hour-rain mr-regular">' + (rain >= 10 ? rain + '%' : '') + '</span>' +
          '</span>';
      }).join('') + '</span>';
    }

    function contentHtml(widget, local, node) {
      var media = runtime && runtime.media ? runtime.media : {};
      var wifi = runtime && runtime.wifi ? runtime.wifi : {};
      var automation = runtime && runtime.automation ? runtime.automation : null;
      var value;
      switch (widget.type) {
        case 'clock': return clockHtml(local);
        case 'date': return escapeHtml(formatDate(local));
        case 'name': return escapeHtml(runtime && runtime.displayName ? runtime.displayName : 'Mirror');
        case 'note': return noteHtml(widget, node);
        case 'weather': return weatherHtml();
        case 'forecast': return forecastHtml(widget);
        case 'wifi':
          return metric('Wi-Fi', escapeHtml(wifi.connected ? (wifi.ssid || 'Online') : 'Offline'), !wifi.connected);
        case 'media':
          value = media.title || (media.state && media.state !== 'idle' ? media.state : '');
          return metric('Media', value ? escapeHtml(value) : 'Nothing playing', !value);
        case 'schedule':
          value = scheduleValue(automation);
          return metric('Schedule', escapeHtml(value.text), value.quiet);
        case 'brightness':
          value = runtime && typeof runtime.brightness === 'number' ? runtime.brightness : null;
          return metric('Brightness', value == null ? '\u2014' : Math.round(value / 255 * 100) + '%', value == null);
        case 'fcast': return metric('Cast', 'Ready', false);
        case 'uptime':
          return metric('Uptime', formatUptime(runtime && runtime.deviceUptimeSeconds), false);
        case 'motion':
          value = motionValue(automation);
          return metric('Presence', escapeHtml(value.text), value.quiet);
        case 'pairing':
          value = runtime && runtime.pairingCode ? String(runtime.pairingCode) : (editing ? '482913' : '');
          return metric('Pair with code', value
            ? '<span class="mr-code">' + escapeHtml(value.slice(0, 3) + ' ' + value.slice(3)) + '</span>'
            : '\u2014', !value);
        case 'photo': return photoMarkup(widget);
        default: return '';
      }
    }

    /* ---- Notes ---- */

    /* The texts a note widget shows right now. Everything but 'text' reads the
       NoteBook the host hands over as runtime.notes (newest first). */
    function noteItems(widget, node) {
      var source = widget.source || 'text';
      var notes = runtime && runtime.notes ? runtime.notes : [];
      var texts = [];
      if (source === 'text') {
        if (widget.text) texts.push(widget.text);
        return texts;
      }
      if (source === 'pinned') {
        for (var index = 0; index < notes.length; index++) {
          if (notes[index].id === widget.note) {
            texts.push(notes[index].text);
            break;
          }
        }
        return texts;
      }
      if (!notes.length) return texts;
      if (source === 'latest') {
        texts.push(notes[0].text);
      } else if (source === 'rotate') {
        texts.push(notes[(slideIndex + (node && node.noteOrder ? node.noteOrder : 0)) % notes.length].text);
      } else if (source === 'list') {
        for (var position = 0; position < notes.length; position++) texts.push(notes[position].text);
      }
      return texts;
    }

    function rotatesNotes(widget) {
      return widget.type === 'note' && widget.source === 'rotate';
    }

    function noteHtml(widget, node) {
      var items = noteItems(widget, node);
      if (!items.length) {
        return editing && (widget.source || 'text') !== 'text'
          ? '<span class="mr-note-hint mr-medium">Notes</span>'
          : '';
      }
      var html = '';
      for (var index = 0; index < items.length; index++) {
        html += '<span class="mr-note-item">' + escapeHtml(items[index]) + '</span>';
      }
      return html;
    }

    /* Notes change while people watch, so swap their words through a short
       fade instead of snapping. A newer change within the fade wins. */
    function fadeSwap(node, widget, html) {
      node.content = html;
      node.pendingHtml = html;
      node.inner.className = 'mr-inner mr-fading';
      window.setTimeout(function () {
        if (node.pendingHtml !== html || !nodes[widget.id]) return;
        node.pendingHtml = null;
        node.inner.innerHTML = html;
        fitContent(node, widget, null);
        /* Markup that arrived faded out has to be laid out once in that
           state, or it would appear at full strength instead of fading in. */
        void node.inner.offsetHeight;
        node.inner.className = 'mr-inner';
      }, 450);
    }

    /* ---- Board ---- */

    function boardFontSize(widget) {
      var stage = stageSize();
      return Math.min(stage.width, stage.height) * (BOARD_SIZES[widget.size] || BOARD_SIZES.medium);
    }

    /* The items a board widget lists right now, in the order the Mirror
       gave them. The host hands the board over as runtime.board (the answer
       to GET /api/v1/board); items that have expired or finished lingering
       since it was fetched are dropped here, so the glass never waits for
       the next fetch to let something go. */
    function boardItems(widget) {
      var board = runtime && runtime.board ? runtime.board : null;
      var source = board && board.items ? board.items : [];
      var time = now.getTime();
      var linger = Number(board && board.doneLingerSeconds || 600) * 1000;
      var items = [];
      var index;
      for (index = 0; index < source.length; index++) {
        var item = source[index];
        if (typeof item.expiresAt === 'number' && item.expiresAt <= time) continue;
        if (item.done && typeof item.doneAt === 'number' && time - item.doneAt >= linger) continue;
        items.push(item);
      }
      if (!items.length && editing) {
        for (index = 0; index < SAMPLE_BOARD.length; index++) {
          var sample = SAMPLE_BOARD[index];
          items.push({
            id: sample.id, kind: sample.kind, title: sample.title, body: sample.body || '',
            done: Boolean(sample.done), priority: sample.priority || 'normal',
            due: sample.dueIn ? time - (time % 60000) + sample.dueIn * 60000 : null
          });
        }
      }
      var show = widget.show || 'all';
      return show === 'all' ? items : items.filter(function (candidate) {
        return candidate.kind === show;
      });
    }

    function boardDay(time) {
      return Math.floor((time + offsetAt(time, runtime) * 60000) / 86400000);
    }

    /* "3:30 PM" today, then "Tomorrow 3:30 PM", "Sat 3:30 PM", "Oct 12". */
    function boardMoment(due) {
      var local = new Date(due + offsetAt(due, runtime) * 60000);
      var parts = timeParts(local, runtime && runtime.clock24Hour);
      var time = parts.digits + (parts.meridiem ? ' ' + parts.meridiem : '');
      var days = boardDay(due) - boardDay(now.getTime());
      if (days === 0) return time;
      if (days === 1) return 'Tomorrow ' + time;
      if (days === -1) return 'Yesterday ' + time;
      if (days > 1 && days < 7) return WEEKDAYS[local.getUTCDay()].slice(0, 3) + ' ' + time;
      return MONTHS[local.getUTCMonth()].slice(0, 3) + ' ' + local.getUTCDate();
    }

    /* How an item stands against the clock, and the words for it. Matches
       the states the API reports (BoardItems.state), worked out here so a
       countdown moves between fetches. */
    function boardStanding(item) {
      if (item.done) return { state: 'done', when: '' };
      if (typeof item.due !== 'number') return { state: 'open', when: '' };
      var board = runtime && runtime.board ? runtime.board : null;
      var soon = Number(board && board.soonSeconds || 3600) * 1000;
      var ahead = item.due - now.getTime();
      if (ahead > soon) return { state: 'open', when: boardMoment(item.due) };
      if (ahead > 0) return { state: 'soon', when: 'In ' + Math.ceil(ahead / 60000) + ' min' };
      var minutes = Math.floor(-ahead / 60000);
      if (minutes < 1) return { state: 'overdue', when: 'Now' };
      if (minutes < 60) return { state: 'overdue', when: minutes + ' min ago' };
      return {
        state: 'overdue',
        when: (item.kind === 'reminder' ? 'Was ' : 'Overdue \u00b7 ') + boardMoment(item.due)
      };
    }

    function boardRowHtml(item) {
      var standing = boardStanding(item);
      var priority = item.priority === 'high' || item.priority === 'low' ? item.priority : 'normal';
      var mark = BOARD_MARKS[item.done ? 'done' : item.kind] || BOARD_MARKS.note;
      return '<span class="mr-board-row mr-board-' + standing.state + ' mr-board-' + priority + '">' +
        '<span class="mr-board-mark"><svg class="mr-icon-svg" viewBox="0 0 24 24" aria-hidden="true">' +
        mark + '</svg></span><span class="mr-board-text">' +
        '<span class="mr-board-title ' + (priority === 'high' ? 'mr-medium' : 'mr-regular') + '">' +
        escapeHtml(item.title) + '</span>' +
        (standing.when ? '<span class="mr-board-when mr-medium">' + escapeHtml(standing.when) + '</span>' : '') +
        (item.body ? '<span class="mr-board-body">' + escapeHtml(item.body) + '</span>' : '') +
        '</span></span>';
    }

    function boardDotsHtml(count, current) {
      if (count < 2) return '';
      var html = '<span class="mr-board-dots">';
      if (count > 8) return html + (current + 1) + ' / ' + count + '</span>';
      for (var index = 0; index < count; index++) {
        html += '<span class="mr-board-dot' + (index === current ? ' mr-on' : '') + '"></span>';
      }
      return html + '</span>';
    }

    function boardPageHtml(view, pages, current) {
      var rows = '';
      var page = pages[current] || [];
      for (var index = 0; index < page.length; index++) rows += view.rows[page[index]];
      return view.heading + '<span class="mr-board-list">' + rows + '</span>' +
        boardDotsHtml(pages.length, current);
    }

    /* Which rows share a page: lay every row out once, read their heights,
       and fill pages top to bottom. Rows carry their spacing as padding, so
       a height is all a row needs. Returns null while the stage is not laid
       out (a hidden tab of the controls), to be measured again later. */
    function boardPages(node, view, box) {
      var all = [];
      var index;
      for (index = 0; index < view.rows.length; index++) all.push(index);
      node.inner.className = 'mr-inner';
      node.inner.innerHTML = boardPageHtml(view, [all, []], 0);
      var heading = node.inner.querySelector('.mr-board-heading');
      var dots = node.inner.querySelector('.mr-board-dots');
      var rows = node.inner.querySelectorAll('.mr-board-row');
      var heights = [];
      var total = heading ? heading.offsetHeight : 0;
      for (index = 0; index < rows.length; index++) {
        heights.push(rows[index].offsetHeight);
        total += heights[index];
      }
      if (rows.length && !total) return null;
      if (total <= box.height + 1) return [all];
      var room = box.height - (heading ? heading.offsetHeight : 0) - (dots ? dots.offsetHeight : 0);
      var pages = [];
      var page = [];
      var used = 0;
      for (index = 0; index < heights.length; index++) {
        if (page.length && used + heights[index] > room + 1) {
          pages.push(page);
          page = [];
          used = 0;
        }
        page.push(index);
        used += heights[index];
      }
      pages.push(page);
      return pages;
    }

    /* Which rows of a page are new, if the page only gained rows: every row
       it had is still there, in its order. Otherwise null. */
    function gainedRows(before, current, ids) {
      if (!before || before.page !== current || ids.length <= before.ids.length) return null;
      var gained = [];
      var kept = 0;
      for (var index = 0; index < ids.length; index++) {
        if (kept < before.ids.length && ids[index] === before.ids[kept]) kept += 1;
        else gained.push(index);
      }
      return kept === before.ids.length ? gained : null;
    }

    /* The board changes in four ways, and each is shown differently. New
       items or a new size are measured afresh. A page that only gained items
       keeps what it had and fades the new ones in. A turned page, or a page
       whose items changed otherwise, fades. A countdown that only ticked over
       is swapped in place. node.shown is the markup on its way to, or already
       in, the DOM. */
    function renderBoard(node, widget, geometryChanged) {
      var items = boardItems(widget);
      var stage = stageSize();
      var view = {
        heading: widget.text
          ? '<span class="mr-board-heading mr-medium">' + escapeHtml(widget.text) + '</span>'
          : '',
        rows: items.map(boardRowHtml)
      };
      var signature = stage.width + 'x' + stage.height + '\n' + view.heading + '\n' + view.rows.join('\n');
      var previous = node.shown;
      var measured = geometryChanged || signature !== node.boardSignature;
      node.element.style.fontSize = boardFontSize(widget) + 'px';
      if (measured) {
        node.boardSignature = signature;
        node.pendingHtml = null;
        node.boardPages = boardPages(node, view, boxSize(widget));
      }
      var all = [];
      for (var index = 0; index < items.length; index++) all.push(index);
      var pages = node.boardPages || [all];
      var current = pages.length > 1 ? Math.floor(now.getTime() / BOARD_PAGE_MS) % pages.length : 0;
      var html = boardPageHtml(view, pages, current);
      var ids = pages[current].map(function (row) { return items[row].id; });
      var turn = current + '|' + ids.join(',');
      var turned = turn !== node.boardTurn;
      var before = node.boardPage;
      node.boardTurn = turn;
      node.boardPage = { page: current, ids: ids };
      if (!measured && html === previous) return;
      node.shown = html;
      if (turned && previous != null && !editing) {
        var gained = gainedRows(before, current, ids);
        if (gained) {
          /* What was there stays as it is; only what is new arrives. */
          node.content = html;
          node.pendingHtml = null;
          node.inner.className = 'mr-inner';
          node.inner.innerHTML = html;
          var shownRows = node.inner.querySelectorAll('.mr-board-row');
          for (var row = 0; row < gained.length; row++) {
            if (shownRows[gained[row]]) shownRows[gained[row]].className += ' mr-board-new';
          }
          return;
        }
        /* Measuring left its own markup behind; fade from what was showing. */
        if (measured) {
          node.inner.innerHTML = previous;
          void node.inner.offsetHeight;
        }
        fadeSwap(node, widget, html);
        return;
      }
      node.content = html;
      node.pendingHtml = null;
      node.inner.className = 'mr-inner';
      node.inner.innerHTML = html;
    }

    /* ---- Photo frames ---- */

    function photoName(widget, node) {
      if (widget.photo) return widget.photo;
      if (!library.names.length) return '';
      return library.names[(slideIndex + (node ? node.order : 0)) % library.names.length];
    }

    function photoMarkup(widget) {
      var empty = !widget.photo && !library.names.length;
      var layer = '<span class="mr-frame-layer"><img class="mr-frame-img" alt=""></span>';
      return '<span class="mr-frame' + (empty ? ' mr-frame-empty' : '') + '">' +
        layer + layer +
        (empty && editing ? '<span class="mr-frame-hint mr-medium">Photo</span>' : '') +
        '</span>';
    }

    function ensureLibrary() {
      var stale = Date.now() - library.fetchedAt > LIBRARY_TTL_MS;
      if (library.pending || !stale) return;
      library.pending = true;
      listPhotos(function (names) {
        library.pending = false;
        library.fetchedAt = Date.now();
        if (!names) return;
        var changed = names.join('\n') !== library.names.join('\n');
        library.names = names;
        if (changed) render(false);
      });
    }

    function ensureSlideshow() {
      if (slideTimer) return;
      slideTimer = window.setInterval(function () {
        slideIndex += 1;
        var local = localDate(now, runtime);
        Object.keys(nodes).forEach(function (id) {
          var node = nodes[id];
          if (!node.widget) return;
          if (node.widget.type === 'photo' && !node.widget.photo) {
            applyPhoto(node, node.widget);
          } else if (rotatesNotes(node.widget)) {
            renderWidget(node.widget, local, false);
          }
        });
      }, SLIDE_INTERVAL_MS);
    }

    function applyPhoto(node, widget) {
      var frame = node.inner.querySelector('.mr-frame');
      if (!frame) return;
      var box = boxSize(widget);
      frame.style.borderRadius = Math.round(Math.max(3, stageSize().width * 0.012)) + 'px';
      var name = photoName(widget, node);
      if (!widget.photo) ensureSlideshow();
      /* Re-request only when the photo or the variant-relevant size changes. */
      var bucket = Math.max(box.width, box.height) * pixelRatio <= 320 ? 'small' : 'large';
      var key = name + '|' + bucket;
      if (key === node.photoKey) return;
      node.photoKey = key;
      node.photoToken = (node.photoToken || 0) + 1;
      var token = node.photoToken;
      if (!name) {
        showFrameImage(node, '', token);
        return;
      }
      resolvePhoto(name, function (url) {
        if (token !== node.photoToken) return;
        showFrameImage(node, url || '', token);
      }, box);
    }

    /* The hidden layer's image loads the next photo itself, then the two
       layers crossfade; one request, and the swap happens only once pixels
       exist. The fade runs on the wrapper span, never on the <img>: Chromium
       before 48 hands a composited <img> straight to the GPU and ignores
       object-fit, so fading the image itself stretched it mid-transition. */
    function showFrameImage(node, url, token) {
      var layers = node.inner.querySelectorAll('.mr-frame-layer');
      if (layers.length < 2) return;
      var front = layers[0].className.indexOf('mr-on') >= 0 ? layers[0] : layers[1];
      var back = front === layers[0] ? layers[1] : layers[0];
      var image = back.querySelector('.mr-frame-img');
      if (!url || !image) {
        layers[0].className = 'mr-frame-layer';
        layers[1].className = 'mr-frame-layer';
        return;
      }
      image.onload = function () {
        if (token !== node.photoToken) return;
        back.className = 'mr-frame-layer mr-on';
        front.className = 'mr-frame-layer';
      };
      image.onerror = function () {
        if (token !== node.photoToken) return;
        back.className = 'mr-frame-layer';
      };
      image.src = url;
    }

    function geometryKey(widget) {
      return [widget.x, widget.y, widget.w, widget.h, widget.align, widget.opacity,
        widget.layer, widget.visible, widget.locked, widget.type, widget.photo || '',
        widget.fit || '', widget.source || '', widget.note || '', widget.size || '',
        widget.weight || '', widget.show || ''].join('|');
    }

    /* What of a widget cannot change smoothly: its size and what it shows.
       Where it is and how strong can, and a style sheet glides those. */
    function shapeKey(widget) {
      return [widget.w, widget.h, widget.align, widget.locked, widget.type, widget.photo || '',
        widget.fit || '', widget.source || '', widget.note || '', widget.size || '',
        widget.weight || '', widget.show || '', layout.textColor, layout.accentColor].join('|');
    }

    function setPhase(node, phase, on) {
      node[phase] = on;
      var name = ' mr-' + phase;
      var classes = node.element.className.split(name).join('');
      node.element.className = classes + (on ? name : '');
    }

    /* A widget that was not there fades in. It is laid out once unseen, or
       it would simply be there. */
    function arrive(node) {
      void node.element.offsetWidth;
      setPhase(node, 'entering', false);
    }

    /* A widget that is no longer wanted fades out, and is taken away once
       it cannot be seen. One that is wanted again before that stays. */
    function leave(id) {
      var node = nodes[id];
      if (node.leaving) return;
      setPhase(node, 'leaving', true);
      node.leaveTimer = window.setTimeout(function () {
        if (nodes[id] !== node || !node.leaving) return;
        widgetLayer.removeChild(node.element);
        delete nodes[id];
      }, LEAVE_MS);
    }

    function stay(node) {
      if (!node.leaving) return;
      window.clearTimeout(node.leaveTimer);
      setPhase(node, 'leaving', false);
    }

    /* A widget whose size or kind of content changed goes dark, changes,
       and comes back: text that is refitted in view jumps. Whatever is
       asked of it while it is dark is what it comes back as. */
    function dissolve(id) {
      var node = nodes[id];
      setPhase(node, 'dissolving', true);
      window.setTimeout(function () {
        if (nodes[id] !== node) return;
        node.geometry = '';
        node.settling = true;
        renderWidget(node.widget, localDate(now, runtime), false);
        node.settling = false;
        void node.element.offsetWidth;
        setPhase(node, 'dissolving', false);
      }, DISSOLVE_MS);
    }

    function applyGeometry(node, widget) {
      var element = node.element;
      var isPhoto = widget.type === 'photo';
      var isNote = widget.type === 'note';
      element.className = 'mr-widget mr-' + widget.type +
        (TEXT_TYPES[widget.type] || isPhoto ? '' : ' mr-metric') +
        (isPhoto && widget.fit === 'contain' ? ' mr-fit-contain' : '') +
        (isNote && NOTE_WEIGHTS[widget.weight] ? ' mr-' + widget.weight : '') +
        (isNote && NOTE_SIZES[widget.size] ? ' mr-note-fixed' : '') +
        ' mr-align-' + widget.align +
        (widget.visible ? '' : ' mr-hidden') +
        (widget.locked ? ' mr-locked' : '') +
        (editing && widget.id === selectedId ? ' mr-selected' : '') +
        (node.entering ? ' mr-entering' : '') +
        (node.leaving ? ' mr-leaving' : '') +
        (node.dissolving ? ' mr-dissolving' : '') +
        (node.covered ? ' mr-covered' : '');
      element.style.left = (widget.x / 10) + '%';
      element.style.top = (widget.y / 10) + '%';
      element.style.width = (widget.w / 10) + '%';
      element.style.height = (widget.h / 10) + '%';
      element.style.opacity = String(widget.opacity / 100);
      element.style.zIndex = String(2 + Number(widget.layer || 0));
      element.style.color = TEXT_TYPES[widget.type] ? layout.textColor : layout.accentColor;
    }

    function ensureNode(widget) {
      var node = nodes[widget.id];
      if (node) return node;
      var element = document.createElement('div');
      var inner = document.createElement('span');
      inner.className = 'mr-inner';
      element.appendChild(inner);
      if (editing) {
        element.setAttribute('data-widget-id', widget.id);
        element.setAttribute('tabindex', '0');
        element.setAttribute('role', 'button');
        var handle = document.createElement('span');
        handle.className = 'mr-handle';
        handle.setAttribute('aria-hidden', 'true');
        element.appendChild(handle);
      }
      widgetLayer.appendChild(element);
      node = { element: element, inner: inner, geometry: '', content: null, type: widget.type };
      nodes[widget.id] = node;
      return node;
    }

    function renderWidget(widget, local, force) {
      var node = ensureNode(widget);
      node.widget = widget;
      if (node.dissolving && !node.settling && !force) return;
      var geometry = geometryKey(widget) + '|' + layout.textColor + '|' + layout.accentColor;
      var geometryChanged = force || geometry !== node.geometry;
      if (geometryChanged) {
        var shape = shapeKey(widget);
        if (!editing && !force && !node.settling && !node.entering
            && node.shape != null && shape !== node.shape) {
          dissolve(widget.id);
          return;
        }
        node.shape = shape;
        node.geometry = geometry;
        applyGeometry(node, widget);
        if (editing) {
          node.element.setAttribute('aria-label', widget.type + ' widget');
        }
      }
      if (widget.type === 'board') {
        renderBoard(node, widget, geometryChanged);
        return;
      }
      var html = contentHtml(widget, local, node);
      var contentChanged = html !== node.content;
      if (widget.type === 'photo') {
        /* Frames keep their layers across geometry changes so images never flash. */
        if (contentChanged) {
          node.content = html;
          node.inner.innerHTML = html;
          node.photoKey = '';
        }
        if (contentChanged || geometryChanged) applyPhoto(node, widget);
        return;
      }
      if (widget.type === 'note' && contentChanged && !geometryChanged
          && !editing && node.content != null) {
        fadeSwap(node, widget, html);
        return;
      }
      if (contentChanged || geometryChanged) {
        /* Re-seed the markup so any earlier trimming is reconsidered. */
        node.content = html;
        node.pendingHtml = null;
        node.inner.className = 'mr-inner';
        node.inner.innerHTML = html;
        fitContent(node, widget, widget.type === 'clock' ? clockProbe() : null);
        if (widget.type === 'weather') trimDetail(node, widget);
      }
    }

    function applyBackground() {
      var background = layout.background;
      var videoSelection = runtime && runtime.backgroundVideos;
      var activeVideo = videoSelection && videoSelection.active;
      var activeVideoId = activeVideo && activeVideo.id ? activeVideo.id : '';
      var key = [background.mode, background.primary, background.secondary,
        background.photo, background.fit, background.dim, activeVideoId, nativeVideo].join('|');
      if (key === backgroundKey) return;
      backgroundKey = key;
      if (background.mode === 'video' && nativeVideo && activeVideoId) {
        container.style.background = 'transparent';
      } else {
        container.style.background = background.mode === 'gradient'
          ? 'linear-gradient(155deg, ' + background.primary + ' 0%, '
            + background.secondary + ' 100%)'
          : background.mode === 'video' ? '#000' : background.primary;
      }
      shadeLayer.style.background = 'rgba(0, 0, 0, ' + (Number(background.dim || 0) / 100) + ')';
      photoToken += 1;
      var token = photoToken;
      photoLayer.className = 'mr-layer mr-backdrop';
      photoLayer.style.backgroundImage = '';
      photoLayer.style.backgroundSize = 'cover';
      if (background.mode === 'photo' && background.photo) {
        resolvePhoto(background.photo, function (url) {
          if (token !== photoToken || !url) return;
          photoLayer.style.backgroundImage = 'url("' + url + '")';
          photoLayer.className = 'mr-layer mr-backdrop mr-ready';
        }, stageSize());
      } else if (background.mode === 'video' && activeVideoId && !nativeVideo) {
        photoLayer.style.backgroundSize =
          background.fit === 'contain' ? 'contain' : 'cover';
        resolveVideoPoster(activeVideoId, function (url) {
          if (token !== photoToken || !url) return;
          photoLayer.style.backgroundImage = 'url("' + url + '")';
          photoLayer.className = 'mr-layer mr-backdrop mr-ready';
        }, stageSize());
      }
    }

    /* Moments. A moment is described, never programmed: the Mirror checks
       what one may hold (Moments.java), and every word of it is escaped
       here. Each of the five kinds is drawn from its fields. */

    /* Width and height of each kind at each size, in thousandths of the
       glass. The glass is portrait, so a drawing that is square to the eye
       is about half as many units high as wide. A list is as high as its rows. */
    var MOMENT_BOXES = {
      text: { small: [460, 90], medium: [640, 140], large: [880, 220] },
      countdown: { small: [300, 100], medium: [440, 150], large: [640, 220] },
      chart: { small: [460, 150], medium: [640, 200], large: [880, 270] },
      drawing: { small: [300, 169], medium: [460, 259], large: [700, 394] },
      list: { small: [520, 36], medium: [700, 44], large: [900, 56] }
    };
    var MOMENT_SIZES = ['large', 'medium', 'small'];
    var MOMENT_EDGE = 40;
    var MOMENT_GAP = 15;
    /* Where the eye rests on a tall glass: a little above the middle. */
    var MOMENT_EYE = 450;
    /* The middle of each named height, and the band the answer takes at each. */
    var MOMENT_CENTRES = { upper: 300, middle: 480, lower: 660 };
    var ANSWER_BANDS = { top: [0, 260], upper: [150, 410], middle: [370, 630], lower: [590, 850], bottom: [720, 1000] };

    function momentSize(moment, size) {
      var box = MOMENT_BOXES[moment.kind][size];
      if (moment.kind !== 'list') return { w: box[0], h: box[1] };
      return { w: box[0], h: Math.min(600, Math.round((moment.rows.length + (moment.title ? 1.2 : 0)) * box[1] + 16)) };
    }

    /* What the widgets draw now, in thousandths of the glass: their words
       and pictures, not their boxes, which are often far larger. */
    function widgetBoxes() {
      var stage = container.getBoundingClientRect();
      var boxes = [];
      Object.keys(nodes).forEach(function (id) {
        var node = nodes[id];
        if (node.leaving) return;
        var rect = (node.type === 'photo' ? node.element : node.inner).getBoundingClientRect();
        if (rect.width < 1 || rect.height < 1) return;
        boxes.push({
          node: node,
          x: (rect.left - stage.left) * 1000 / stage.width,
          y: (rect.top - stage.top) * 1000 / stage.height,
          w: rect.width * 1000 / stage.width,
          h: rect.height * 1000 / stage.height
        });
      });
      return boxes;
    }

    /* Where the moments that are showing lie, but for one. */
    function momentBoxes(except) {
      return Object.keys(momentNodes).filter(function (id) {
        return id !== except && !momentNodes[id].leaving && momentNodes[id].box;
      }).map(function (id) { return momentNodes[id].box; });
    }

    /* Whether two boxes come closer to one another than the gap. */
    function touches(a, b, gap) {
      return a.x < b.x + b.w + gap && b.x < a.x + a.w + gap && a.y < b.y + b.h + gap && b.y < a.y + a.h + gap;
    }

    /* Where a moment goes. A height that was named is taken as said.
       Otherwise it goes as near as it can to where the eye rests, in the
       first of these that there is: free room; room that widgets have, which
       step back while it shows (a moment is there for a while, and what was
       asked for should not be the thing that is made small); room at a
       smaller size; room where the answer will lie over it for a while. Moments never lie over
       one another. With no room at all there is no place, unless one is to
       be had come what may. */
    function placeMoment(moment, comeWhatMay) {
      var across = function (size, side) {
        return side === 'left' ? MOMENT_EDGE : side === 'right' ? 1000 - MOMENT_EDGE - size.w : Math.round((1000 - size.w) / 2);
      };
      var asked = momentSize(moment, moment.size);
      var middle = function (height) {
        var y = height === 'top' ? MOMENT_EDGE : height === 'bottom' ? 1000 : Math.round(MOMENT_CENTRES[height] - asked.h / 2);
        return { x: across(asked, moment.side), y: Math.max(MOMENT_EDGE, Math.min(1000 - MOMENT_EDGE - asked.h, y)), w: asked.w, h: asked.h };
      };
      if (moment.height) return middle(moment.height);
      var others = momentBoxes(moment.id);
      var assistant = runtime && runtime.assistant;
      var band = assistant && assistant.enabled && assistant.place && ANSWER_BANDS[assistant.place.height];
      var answer = band ? { x: 0, y: band[0], w: 1000, h: band[1] - band[0] } : null;
      var smaller = MOMENT_SIZES.slice(MOMENT_SIZES.indexOf(moment.size) + 1);
      var columns = moment.side ? [moment.side] : ['center', 'left', 'right'];
      var ways = [
        { sizes: [moment.size], clearOf: others.concat(widgetBoxes()) },
        { sizes: [moment.size], clearOf: others },
        { sizes: smaller, clearOf: others },
        { sizes: [moment.size].concat(smaller), clearOf: others, underAnswer: true }
      ];
      for (var w = 0; w < ways.length; w++) {
        var way = ways[w];
        for (var s = 0; s < way.sizes.length; s++) {
          var size = momentSize(moment, way.sizes[s]);
          var off = function (y) { return Math.abs(y + size.h / 2 - MOMENT_EYE); };
          var rows = [];
          for (var y = MOMENT_EDGE; y + size.h <= 1000 - MOMENT_EDGE; y += 20) rows.push(y);
          rows.sort(function (a, b) { return off(a) - off(b); });
          for (var c = 0; c < columns.length; c++) {
            for (var r = 0; r < rows.length; r++) {
              var box = { x: across(size, columns[c]), y: rows[r], w: size.w, h: size.h };
              if (!way.underAnswer && answer && touches(box, answer, 0)) continue;
              if (!way.clearOf.some(function (other) { return touches(box, other, MOMENT_GAP); })) return box;
            }
          }
        }
      }
      return comeWhatMay ? middle('middle') : null;
    }

    /* A widget that a moment lies over steps back for as long as it does:
       words over words cannot be read. It returns when the moment leaves. */
    function coverWidgets() {
      var shown = momentBoxes(null);
      if (shown.length === 0) {
        Object.keys(nodes).forEach(function (id) {
          if (nodes[id].covered) setPhase(nodes[id], 'covered', false);
        });
        return;
      }
      widgetBoxes().forEach(function (box) {
        var covered = shown.some(function (other) { return touches(box, other, 0); });
        if (Boolean(box.node.covered) !== covered) setPhase(box.node, 'covered', covered);
      });
    }

    /* A full glass: the newest moment is what someone has just asked for,
       so the oldest ones leave until it has room. The Mirror is told, so
       that what it lists is what shows. */
    function roomFor(moment) {
      for (;;) {
        var box = placeMoment(moment, false);
        if (box) return box;
        var oldest = null;
        Object.keys(momentNodes).forEach(function (id) {
          var node = momentNodes[id];
          if (id === moment.id || node.leaving || !node.box) return;
          if (!oldest || node.moment.createdAt < momentNodes[oldest].moment.createdAt) oldest = id;
        });
        if (!oldest) return placeMoment(moment, true);
        displaced[oldest] = momentNodes[oldest].moment.createdAt;
        dismissMoment(oldest);
        dropMoment(oldest);
      }
    }

    function countdownText(left) {
      var seconds = Math.max(0, Math.ceil(left / 1000));
      var hours = Math.floor(seconds / 3600);
      var minutes = Math.floor(seconds % 3600 / 60);
      return (hours ? hours + ':' + pad(minutes) : String(minutes)) + ':' + pad(seconds % 60);
    }

    function rowsHtml(moment) {
      return '<div class="mr-m-rows">' + moment.rows.map(function (row) {
        return '<div class="mr-m-row">' +
          (row.label ? '<span class="mr-m-label">' + escapeHtml(row.label) + '</span>' : '') +
          '<span>' + escapeHtml(row.text) + '</span></div>';
      }).join('') + '</div>';
    }

    function chartHtml(moment) {
      var values = moment.values.map(function (entry) { return Number(entry.value); });
      var high = Math.max.apply(null, values);
      var low = Math.min.apply(null, values);
      /* The bars stand on zero where their difference still shows; where it
         would not, as with a day's temperatures, on a floor a little under the least. */
      var floor = low >= 0 && low <= high * 0.5 ? 0 : low - ((high - low) || 1) * 0.25;
      var span = (high - floor) || 1;
      var line = moment.chart === 'line';
      var step = 100 / values.length;
      var cells = function (name) {
        return '<div class="mr-m-cells">' + moment.values.map(function (entry) {
          return '<span>' + escapeHtml(name === 'value' ? String(Math.round(entry.value * 10) / 10) : entry.label) + '</span>';
        }).join('') + '</div>';
      };
      var points = [];
      var marks = values.map(function (value, index) {
        var share = (value - floor) / span * 100;
        var middle = (index + 0.5) * step;
        points.push(middle.toFixed(2) + ',' + (100 - share).toFixed(2));
        /* Where a mark stands: from the left, from the bottom, and for a bar how wide and how high. */
        return line
          ? '<span class="mr-m-dot" data-place="' + middle.toFixed(2) + ' ' + share.toFixed(2) + '"></span>'
          : '<span class="mr-m-bar" data-place="' + (middle - step * 0.3).toFixed(2) + ' 0 ' + (step * 0.6).toFixed(2) +
            ' ' + share.toFixed(2) + '"></span>';
      }).join('');
      var path = line
        ? '<svg viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points="' + points.join(' ') +
          '" fill="none" stroke="currentColor" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>'
        : '';
      return cells('value') + '<div class="mr-m-plot">' + path + marks + '</div>' + cells('label');
    }

    function shapeHtml(shape) {
      var words = shape.shape === 'text';
      var number = function (name) { return String(Number(shape[name]) || 0); };
      var paint = ' stroke="' + escapeHtml(shape.stroke || (words ? 'none' : 'currentColor')) +
        '" fill="' + escapeHtml(shape.fill || (words ? 'currentColor' : 'none')) +
        '" stroke-width="' + (Number(shape.width) || 1.2) + '"';
      switch (shape.shape) {
        case 'line':
          return '<line x1="' + number('x1') + '" y1="' + number('y1') + '" x2="' + number('x2') + '" y2="' + number('y2') + '"' + paint + '/>';
        case 'circle':
          return '<circle cx="' + number('x') + '" cy="' + number('y') + '" r="' + number('r') + '"' + paint + '/>';
        case 'rect':
          return '<rect x="' + number('x') + '" y="' + number('y') + '" width="' + number('w') + '" height="' + number('h') +
            '" rx="' + number('round') + '" ry="' + number('round') + '"' + paint + '/>';
        case 'path':
          return '<path d="' + escapeHtml(shape.d) + '"' + paint + '/>';
        default:
          return '<text x="' + number('x') + '" y="' + number('y') + '" font-size="' + number('size') +
            '" text-anchor="middle"' + paint + '>' + escapeHtml(shape.text) + '</text>';
      }
    }

    /* What a moment holds, and the size its words start from: a share of
       its box, which they then shrink from until they fit. */
    function momentBody(moment, box) {
      switch (moment.kind) {
        case 'countdown':
          return {
            html: '<div class="mr-m-count mr-thin"></div><div class="mr-m-track"><span></span></div>',
            size: Math.min(box.height * 0.58, box.width * 0.27)
          };
        case 'list':
          return {
            html: rowsHtml(moment),
            size: Math.min(box.height / (moment.rows.length + (moment.title ? 1.2 : 0)) * 0.58, box.width * 0.075)
          };
        case 'chart':
          return { html: chartHtml(moment), size: Math.min(box.height * 0.1, box.width / moment.values.length * 0.3) };
        case 'drawing':
          return {
            html: '<div class="mr-m-canvas"><svg viewBox="0 0 100 100" stroke-linecap="round" stroke-linejoin="round">' +
              moment.shapes.map(shapeHtml).join('') + '</svg></div>',
            size: box.height * 0.09
          };
        default:
          return {
            html: '<div class="mr-m-text">' + escapeHtml(moment.text) + '</div>',
            size: Math.min(box.height * 0.6, box.width * 0.22)
          };
      }
    }

    function drawMoment(node) {
      var moment = node.moment;
      var element = node.element;
      var stage = stageSize();
      /* It keeps its place while it shows, unless what decides its place has changed. */
      var placed = [moment.kind, moment.size, moment.height, moment.side, moment.rows ? moment.rows.length : 0, Boolean(moment.title)].join('|');
      if (node.placed !== placed) {
        node.placed = placed;
        node.box = roomFor(moment);
      }
      var box = { width: stage.width * node.box.w / 1000, height: stage.height * node.box.h / 1000 };
      element.style.left = (node.box.x / 10) + '%';
      element.style.top = (node.box.y / 10) + '%';
      element.style.width = (node.box.w / 10) + '%';
      element.style.height = (node.box.h / 10) + '%';
      element.style.color = moment.color || layout.textColor;
      var body = momentBody(moment, box);
      node.done = false;
      node.inner.className = 'mr-m-inner' + (moment.motion && moment.motion !== 'none' ? ' mr-m-' + moment.motion : '');
      node.inner.innerHTML = (moment.title ? '<div class="mr-m-title">' + escapeHtml(moment.title) + '</div>' : '') + body.html;
      /* The page takes no style that is written into markup, so a chart's marks are placed here. */
      Array.prototype.forEach.call(node.inner.querySelectorAll('[data-place]'), function (mark) {
        var place = mark.getAttribute('data-place').split(' ');
        mark.style.left = place[0] + '%';
        mark.style.bottom = place[1] + '%';
        if (place.length > 2) {
          mark.style.width = place[2] + '%';
          mark.style.height = place[3] + '%';
        }
      });
      var size = body.size;
      element.style.fontSize = size + 'px';
      if (moment.kind === 'countdown') tickMoment(node, Date.now());
      /* What it holds has to fit inside its backing's margin. A line that is
         centred sticks out to both sides, and only the right one counts as
         scrolling, so each part's own width is looked at as well. */
      var tooLarge = function () {
        var inner = node.inner;
        if (inner.scrollHeight > inner.clientHeight + 1 || inner.scrollWidth > inner.clientWidth + 1) return true;
        return Array.prototype.some.call(inner.children, function (part) { return part.offsetWidth > inner.clientWidth + 1; });
      };
      for (var tries = 0; tries < 24 && tooLarge(); tries++) {
        size *= 0.9;
        element.style.fontSize = size + 'px';
      }
    }

    /* A countdown shows what is left, and a line under it that shortens. */
    function tickMoment(node, time) {
      var moment = node.moment;
      var left = moment.endsAt - time;
      var digits = node.inner.querySelector('.mr-m-count');
      var text = countdownText(left);
      if (digits.textContent !== text) digits.textContent = text;
      var share = Math.max(0, Math.min(1, left / Math.max(1, moment.endsAt - moment.createdAt)));
      node.inner.querySelector('.mr-m-track span').style.width = (share * 100).toFixed(2) + '%';
      if (left <= 0 && !node.done) {
        node.done = true;
        node.inner.className += ' mr-m-done';
      }
    }

    /* A moment that is no longer wanted, or whose time is up, fades out and is then taken away. */
    function dismissMoment(id) {
      var node = momentNodes[id];
      if (node.leaving) return;
      node.leaving = true;
      node.element.className += ' mr-leaving';
      coverWidgets();
      node.leaveTimer = window.setTimeout(function () {
        if (momentNodes[id] !== node || !node.leaving) return;
        momentLayer.removeChild(node.element);
        delete momentNodes[id];
      }, LEAVE_MS);
    }

    function renderMoments() {
      var list = (!editing && runtime && runtime.moments) || [];
      var time = Date.now();
      var stage = stageSize();
      var seen = {};
      list.forEach(function (moment) {
        if (moment.until <= time || displaced[moment.id] === moment.createdAt) return;
        seen[moment.id] = true;
        var node = momentNodes[moment.id];
        var arriving = !node;
        if (arriving) {
          node = momentNodes[moment.id] = { element: document.createElement('div'), inner: document.createElement('div') };
          node.element.className = 'mr-moment mr-entering';
          node.element.appendChild(node.inner);
          momentLayer.appendChild(node.element);
        }
        var key = JSON.stringify(moment) + layout.textColor + stage.width + 'x' + stage.height;
        if (node.key !== key) {
          node.key = key;
          node.moment = moment;
          drawMoment(node);
        }
        /* It is laid out once unseen, or it would simply be there. */
        if (arriving) void node.element.offsetWidth;
        window.clearTimeout(node.leaveTimer);
        node.leaving = false;
        node.element.className = 'mr-moment mr-m-' + moment.kind;
      });
      Object.keys(momentNodes).forEach(function (id) {
        if (!seen[id]) dismissMoment(id);
      });
      coverWidgets();
      /* One that the Mirror no longer lists need not be remembered. */
      Object.keys(displaced).forEach(function (id) {
        if (!list.some(function (moment) { return moment.id === id && moment.createdAt === displaced[id]; })) delete displaced[id];
      });
    }

    function tickMoments() {
      var time = Date.now();
      Object.keys(momentNodes).forEach(function (id) {
        var node = momentNodes[id];
        if (node.leaving) return;
        /* The glass lets a moment go at its second; the Mirror forgets it by itself. */
        if (node.moment.until <= time) dismissMoment(id);
        else if (node.moment.kind === 'countdown') tickMoment(node, time);
      });
    }

    function render(force) {
      if (!layout) return;
      var local = localDate(now, runtime);
      applyBackground();
      var seen = {};
      var rotating = 0;
      var rotatingNotes = 0;
      layout.widgets.forEach(function (widget) {
        var showsWeather = widget.type !== 'weather' && widget.type !== 'forecast'
          || Boolean(weatherData(runtime, editing));
        if (widget.type === 'photo' && !widget.photo && (widget.visible || editing)) {
          ensureLibrary();
          /* Offset rotating frames so a collage never repeats one photo. */
          var existing = nodes[widget.id];
          if (existing) existing.order = rotating;
          rotating += 1;
        }
        if (rotatesNotes(widget) && (widget.visible || editing)) {
          ensureSlideshow();
          var existingNote = nodes[widget.id];
          if (existingNote) existingNote.noteOrder = rotatingNotes;
          rotatingNotes += 1;
        }
        var showsPhoto = widget.type !== 'photo' || Boolean(widget.photo) || library.names.length > 0;
        var showsNote = widget.type !== 'note' || noteItems(widget, nodes[widget.id]).length > 0;
        var showsBoard = widget.type !== 'board' || boardItems(widget).length > 0;
        if (!editing && (!widget.visible || !showsWeather || !showsPhoto || !showsNote || !showsBoard)) return;
        seen[widget.id] = true;
        var arriving = !nodes[widget.id] && drawn && !editing && !force;
        var node = ensureNode(widget);
        if (widget.type === 'photo' && node.order == null) node.order = rotating - 1;
        if (rotatesNotes(widget) && node.noteOrder == null) node.noteOrder = rotatingNotes - 1;
        if (arriving) node.entering = true;
        stay(node);
        renderWidget(widget, local, force);
        if (arriving) arrive(node);
      });
      Object.keys(nodes).forEach(function (id) {
        if (seen[id]) return;
        if (drawn && !editing && !force) {
          leave(id);
        } else {
          window.clearTimeout(nodes[id].leaveTimer);
          widgetLayer.removeChild(nodes[id].element);
          delete nodes[id];
        }
      });
      /* After the widgets, so that a new moment finds what is drawn where it is. */
      renderMoments();
      drawn = true;
    }

    function tick(date) {
      now = date || new Date();
      if (!layout) return;
      tickMoments();
      var local = localDate(now, runtime);
      layout.widgets.forEach(function (widget) {
        /* The board keeps time too: its pages turn and its countdowns run. */
        /* What is on its way out keeps what it showed until it is gone. */
        if ((widget.type === 'clock' || widget.type === 'date' || widget.type === 'board')
            && nodes[widget.id] && !nodes[widget.id].leaving) {
          renderWidget(widget, local, false);
        }
      });
    }

    return {
      update: function (nextLayout, nextRuntime) {
        if (nextLayout) layout = nextLayout;
        if (nextRuntime) runtime = nextRuntime;
        render(false);
      },
      tick: tick,
      relayout: function () { render(true); },
      setSelected: function (id) {
        selectedId = id || '';
        render(true);
      },
      element: function (id) {
        return nodes[id] ? nodes[id].element : null;
      },
      refreshPhotos: function () {
        library.fetchedAt = 0;
        Object.keys(nodes).forEach(function (id) { nodes[id].photoKey = ''; });
        render(true);
      },
      stage: container,
      layer: widgetLayer
    };
  }

  global.MirrorRenderer = {
    create: create,
    timeParts: timeParts,
    formatDate: formatDate,
    iconName: iconName,
    escapeHtml: escapeHtml
  };
}(window));
