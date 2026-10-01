/* Offline fallback clock. Runs on the Mirror's Chromium 44 WebView: ES5 only. */
(function () {
  'use strict';

  var utcOffsetMinutes = 0;
  var nextUtcOffsetChange = null;
  var clock24Hour = false;
  var WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
    'September', 'October', 'November', 'December'];

  function pad(value) { return value < 10 ? '0' + value : String(value); }

  function tick() {
    var now = Date.now();
    var offset = nextUtcOffsetChange && now >= nextUtcOffsetChange.at
      ? Number(nextUtcOffsetChange.utcOffsetMinutes || 0)
      : utcOffsetMinutes;
    var date = new Date(now + offset * 60000);
    var hours = date.getUTCHours();
    var minutes = pad(date.getUTCMinutes());
    document.getElementById('time').textContent = clock24Hour
      ? pad(hours) + ':' + minutes
      : (hours % 12 || 12) + ':' + minutes;
    document.getElementById('meridiem').textContent = clock24Hour ? '' : (hours >= 12 ? 'PM' : 'AM');
    document.getElementById('date').textContent =
      WEEKDAYS[date.getUTCDay()] + ', ' + MONTHS[date.getUTCMonth()] + ' ' + date.getUTCDate();
  }

  function status() {
    fetch('/api/v1/status', { cache: 'no-store' })
      .then(function (response) { return response.json(); })
      .then(function (snapshot) {
        utcOffsetMinutes = Number(snapshot.utcOffsetMinutes || 0);
        nextUtcOffsetChange = snapshot.nextUtcOffsetChange && typeof snapshot.nextUtcOffsetChange.at === 'number'
          ? snapshot.nextUtcOffsetChange
          : null;
        clock24Hour = Boolean(snapshot.clock24Hour);
        tick();
      })
      .then(null, function () {});
  }

  function alignedTick() {
    tick();
    window.setTimeout(alignedTick, 1000 - (Date.now() % 1000) + 15);
  }

  status();
  alignedTick();
  window.setInterval(status, 30000);
}());