/* Aurora ambient clock. Runs on the Mirror's Chromium 44 WebView: ES5 only. */
(function () {
  'use strict';

  var utcOffsetMinutes = 0;
  var clock24Hour = false;
  var WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
    'September', 'October', 'November', 'December'];

  function pad(value) { return value < 10 ? '0' + value : String(value); }

  function tick() {
    var date = new Date(Date.now() + utcOffsetMinutes * 60000);
    var hours = date.getUTCHours();
    var minutes = pad(date.getUTCMinutes());
    if (clock24Hour) {
      document.getElementById('time').textContent = pad(hours) + ':' + minutes;
      document.getElementById('meridiem').textContent = '';
    } else {
      document.getElementById('time').textContent = (hours % 12 || 12) + ':' + minutes;
      document.getElementById('meridiem').textContent = hours >= 12 ? 'PM' : 'AM';
    }
    document.getElementById('date').textContent =
      WEEKDAYS[date.getUTCDay()] + ', ' + MONTHS[date.getUTCMonth()] + ' ' + date.getUTCDate();
  }

  function reveal() {
    var fades = document.querySelectorAll('.fade');
    for (var index = 0; index < fades.length; index++) {
      fades[index].className += ' ready';
    }
  }

  function status() {
    return fetch('/api/v1/status', { cache: 'no-store' })
      .then(function (response) { return response.json(); })
      .then(function (snapshot) {
        document.getElementById('name').textContent = snapshot.displayName || 'Mirror';
        utcOffsetMinutes = Number(snapshot.utcOffsetMinutes || 0);
        clock24Hour = Boolean(snapshot.clock24Hour);
        document.getElementById('status').textContent =
          snapshot.wifi && snapshot.wifi.connected ? '' : 'Offline';
        tick();
      })
      .then(null, function () {
        document.getElementById('status').textContent = 'Offline';
      });
  }

  function alignedTick() {
    tick();
    window.setTimeout(alignedTick, 1000 - (Date.now() % 1000) + 15);
  }

  status().then(reveal, reveal);
  alignedTick();
  window.setInterval(status, 15000);
}());