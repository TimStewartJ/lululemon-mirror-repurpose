/* Shared mirror renderer.
   One source of truth for how the Mirror looks: used by the Mirror's own
   WebView (Chromium 44: strict ES5 only), the layout editor, and the live
   preview in the control application. */
(function (global) {
  'use strict';

  var TEXT_TYPES = { clock: true, date: true, name: true, note: true };
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

  function localDate(now, runtime) {
    var offset = runtime ? Number(runtime.utcOffsetMinutes || 0) : 0;
    return new Date(now.getTime() + offset * 60000);
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
    var listPhotos = settings.listPhotos || function (done) {
      fetch('/api/v1/photos/slideshow', { cache: 'no-store' }).then(function (response) {
        return response.json();
      }).then(function (result) {
        done((result.photos || []).map(function (photo) { return photo.name; }));
      }).then(null, function () { done(null); });
    };

    container.className += (container.className ? ' ' : '') + 'mr-stage' +
      (editing ? ' mr-editing' : '');
    var photoLayer = document.createElement('div');
    photoLayer.className = 'mr-layer mr-backdrop';
    var shadeLayer = document.createElement('div');
    shadeLayer.className = 'mr-layer mr-shade';
    var widgetLayer = document.createElement('div');
    widgetLayer.className = 'mr-layer mr-widgets';
    container.appendChild(photoLayer);
    container.appendChild(shadeLayer);
    container.appendChild(widgetLayer);

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
        case 'note': return Math.min(box.height * 0.3, box.width * 0.075);
        case 'weather': return box.height / 1.55;
        case 'forecast': return forecastFontSize(box);
        default: return box.height / 1.72;
      }
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
      var realHtml = inner.innerHTML;
      if (probeHtml) inner.innerHTML = probeHtml;
      element.style.fontSize = base + 'px';
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
      var offset = runtime ? Number(runtime.utcOffsetMinutes || 0) : 0;
      return '<span class="mr-hours">' + hours.map(function (hour) {
        var date = new Date(Number(hour.time) + offset * 60000);
        var rain = Number(hour.precipitationProbability || 0);
        return '<span class="mr-hour">' +
          '<span class="mr-hour-label mr-medium">' + escapeHtml(hourLabel(date, clock24Hour)) + '</span>' +
          iconSvg(Number(hour.weatherCode), true, 'mr-hour-icon') +
          '<span class="mr-hour-temp mr-light">' + temperature(hour.temperature) + '</span>' +
          '<span class="mr-hour-rain mr-regular">' + (rain >= 10 ? rain + '%' : '') + '</span>' +
          '</span>';
      }).join('') + '</span>';
    }

    function contentHtml(widget, local) {
      var media = runtime && runtime.media ? runtime.media : {};
      var wifi = runtime && runtime.wifi ? runtime.wifi : {};
      var automation = runtime && runtime.automation ? runtime.automation : null;
      var value;
      switch (widget.type) {
        case 'clock': return clockHtml(local);
        case 'date': return escapeHtml(formatDate(local));
        case 'name': return escapeHtml(runtime && runtime.displayName ? runtime.displayName : 'Mirror');
        case 'note': return escapeHtml(widget.text || '');
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
        Object.keys(nodes).forEach(function (id) {
          var node = nodes[id];
          if (node.widget && node.widget.type === 'photo' && !node.widget.photo) {
            applyPhoto(node, node.widget);
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
        widget.fit || ''].join('|');
    }

    function applyGeometry(node, widget) {
      var element = node.element;
      var isPhoto = widget.type === 'photo';
      element.className = 'mr-widget mr-' + widget.type +
        (TEXT_TYPES[widget.type] || isPhoto ? '' : ' mr-metric') +
        (isPhoto && widget.fit === 'contain' ? ' mr-fit-contain' : '') +
        ' mr-align-' + widget.align +
        (widget.visible ? '' : ' mr-hidden') +
        (widget.locked ? ' mr-locked' : '') +
        (editing && widget.id === selectedId ? ' mr-selected' : '');
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
      var geometry = geometryKey(widget) + '|' + layout.textColor + '|' + layout.accentColor;
      var geometryChanged = force || geometry !== node.geometry;
      if (geometryChanged) {
        node.geometry = geometry;
        applyGeometry(node, widget);
        if (editing) {
          node.element.setAttribute('aria-label', widget.type + ' widget');
        }
      }
      var html = contentHtml(widget, local);
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
      if (contentChanged || geometryChanged) {
        /* Re-seed the markup so any earlier trimming is reconsidered. */
        node.content = html;
        node.inner.innerHTML = html;
        fitContent(node, widget, widget.type === 'clock' ? clockProbe() : null);
        if (widget.type === 'weather') trimDetail(node, widget);
      }
    }

    function applyBackground() {
      var background = layout.background;
      var key = [background.mode, background.primary, background.secondary,
        background.photo, background.dim].join('|');
      if (key === backgroundKey) return;
      backgroundKey = key;
      container.style.background = background.mode === 'gradient'
        ? 'linear-gradient(155deg, ' + background.primary + ' 0%, ' + background.secondary + ' 100%)'
        : background.primary;
      shadeLayer.style.background = 'rgba(0, 0, 0, ' + (Number(background.dim || 0) / 100) + ')';
      photoToken += 1;
      var token = photoToken;
      photoLayer.className = 'mr-layer mr-backdrop';
      photoLayer.style.backgroundImage = '';
      if (background.mode === 'photo' && background.photo) {
        resolvePhoto(background.photo, function (url) {
          if (token !== photoToken || !url) return;
          photoLayer.style.backgroundImage = 'url("' + url + '")';
          photoLayer.className = 'mr-layer mr-backdrop mr-ready';
        }, stageSize());
      }
    }

    function render(force) {
      if (!layout) return;
      var local = localDate(now, runtime);
      applyBackground();
      var seen = {};
      var rotating = 0;
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
        var showsPhoto = widget.type !== 'photo' || Boolean(widget.photo) || library.names.length > 0;
        if (!editing && (!widget.visible || !showsWeather || !showsPhoto)) return;
        seen[widget.id] = true;
        var node = ensureNode(widget);
        if (widget.type === 'photo' && node.order == null) node.order = rotating - 1;
        renderWidget(widget, local, force);
      });
      Object.keys(nodes).forEach(function (id) {
        if (!seen[id]) {
          widgetLayer.removeChild(nodes[id].element);
          delete nodes[id];
        }
      });
    }

    function tick(date) {
      now = date || new Date();
      if (!layout) return;
      var local = localDate(now, runtime);
      layout.widgets.forEach(function (widget) {
        if ((widget.type === 'clock' || widget.type === 'date') && nodes[widget.id]) {
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
