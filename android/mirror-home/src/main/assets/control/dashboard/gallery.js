/* Photo gallery. Runs on the Mirror's Chromium 44 WebView: ES5 only. */
(function () {
  'use strict';

  var photos = [];
  var index = -1;
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
    document.getElementById('clock').textContent = clock24Hour
      ? pad(hours) + ':' + minutes
      : (hours % 12 || 12) + ':' + minutes;
    document.getElementById('meridiem').textContent = clock24Hour ? '' : (hours >= 12 ? 'PM' : 'AM');
    document.getElementById('date').textContent =
      WEEKDAYS[date.getUTCDay()] + ', ' + MONTHS[date.getUTCMonth()] + ' ' + date.getUTCDate();
  }

  function next() {
    if (!photos.length) return;
    var images = document.querySelectorAll('.photo');
    index = (index + 1) % photos.length;
    for (var i = 0; i < images.length; i++) {
      images[i].className = 'photo' + (i % 2 ? ' pan-b' : '') + (i === index ? ' visible' : '');
    }
  }

  function sameNames(left, right) {
    if (left.length !== right.length) return false;
    for (var i = 0; i < left.length; i++) {
      if (left[i].name !== right[i].name) return false;
    }
    return true;
  }

  function load() {
    Promise.all([
      fetch('/api/v1/photos/slideshow', { cache: 'no-store' }).then(function (r) { return r.json(); }),
      fetch('/api/v1/status', { cache: 'no-store' }).then(function (r) { return r.json(); })
    ]).then(function (results) {
      var status = results[1];
      utcOffsetMinutes = Number(status.utcOffsetMinutes || 0);
      clock24Hour = Boolean(status.clock24Hour);
      tick();
      var nextPhotos = results[0].photos || [];
      document.getElementById('empty').className = 'empty' + (nextPhotos.length ? ' hidden' : '');
      if (sameNames(photos, nextPhotos)) return;
      photos = nextPhotos;
      var stage = document.getElementById('stage');
      var previous = stage.querySelectorAll('.photo');
      for (var i = 0; i < previous.length; i++) stage.removeChild(previous[i]);
      index = -1;
      photos.forEach(function (photo) {
        var image = document.createElement('img');
        image.className = 'photo';
        image.alt = '';
        image.src = '/photos/' + encodeURIComponent(photo.name) + '/display';
        stage.appendChild(image);
      });
      next();
    }).then(null, function () {});
  }

  function alignedTick() {
    tick();
    window.setTimeout(alignedTick, 1000 - (Date.now() % 1000) + 15);
  }

  load();
  alignedTick();
  window.setTimeout(function () {
    document.querySelector('.caption').className += ' ready';
  }, 250);
  window.setInterval(next, 24000);
  window.setInterval(load, 30000);
}());