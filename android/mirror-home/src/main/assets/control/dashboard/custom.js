/* Built-in dashboard host. Runs on the Mirror's Chromium 44 WebView: ES5 only. */
(function () {
  'use strict';

  var stage = document.getElementById('dashboard');
  var renderer = window.MirrorRenderer.create(stage);
  var layout = null;
  var runtime = null;
  var notes = [];
  var notesVersion = null;
  var notesPending = false;
  var lastLayoutText = '';
  var revealed = false;

  function fetchJson(path) {
    return fetch(path, { cache: 'no-store' }).then(function (response) {
      if (!response.ok) throw new Error('Request failed');
      return response.json();
    });
  }

  function reveal() {
    if (revealed || !layout || !runtime) return;
    revealed = true;
    /* Let the first paint settle before fading up from black. */
    window.setTimeout(function () {
      stage.className += ' mr-ready';
    }, 60);
  }

  function refreshLayout() {
    return fetchJson('/api/v1/dashboard/layout').then(function (next) {
      var text = JSON.stringify(next);
      if (text !== lastLayoutText) {
        layout = next;
        lastLayoutText = text;
        renderer.update(layout, withNotes(runtime));
      }
      reveal();
    });
  }

  /* Notes ride along inside the runtime the renderer already receives; the
     runtime's notesVersion tells us when the NoteBook actually changed. */
  function withNotes(value) {
    if (value) value.notes = notes;
    return value;
  }

  function refreshNotes(version) {
    if (notesPending) return Promise.resolve();
    notesPending = true;
    return fetchJson('/api/v1/notes').then(function (result) {
      notesPending = false;
      notes = result.notes || [];
      notesVersion = typeof result.version === 'number' ? result.version : version;
      renderer.update(layout, withNotes(runtime));
    }).then(null, function () { notesPending = false; });
  }

  function refreshRuntime() {
    return fetchJson('/api/v1/dashboard/runtime').then(function (next) {
      runtime = next;
      renderer.update(layout, withNotes(runtime));
      /* Hold the first reveal until the notes are in so nothing pops in late. */
      var pending = next.notesVersion !== notesVersion
        ? refreshNotes(next.notesVersion)
        : Promise.resolve();
      return pending.then(reveal);
    });
  }

  function swallow() {}

  function alignedTick() {
    renderer.tick(new Date());
    var delay = 1000 - (Date.now() % 1000);
    window.setTimeout(alignedTick, delay + 15);
  }

  Promise.all([refreshLayout(), refreshRuntime()]).then(null, swallow);
  alignedTick();
  window.setInterval(function () { refreshRuntime().then(null, swallow); }, 5000);
  window.setInterval(function () { refreshLayout().then(null, swallow); }, 7000);

  var resizeTimer = null;
  window.addEventListener('resize', function () {
    if (resizeTimer) window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(function () { renderer.relayout(); }, 120);
  });
}());