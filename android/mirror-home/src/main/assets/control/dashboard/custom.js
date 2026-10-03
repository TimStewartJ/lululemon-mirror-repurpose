/* Built-in dashboard host. Runs on the Mirror's Chromium 44 WebView: ES5 only. */
(function () {
  'use strict';

  var stage = document.getElementById('dashboard');
  var renderer = window.MirrorRenderer.create(stage, { nativeVideo: true });
  var layout = null;
  var runtime = null;
  var notes = [];
  var notesVersion = null;
  var notesPending = false;
  var board = null;
  var boardVersion = null;
  var boardFetchedAt = 0;
  var boardPending = false;
  /* The Mirror orders the board by the clock, so ask again now and then
     even when nothing was posted. */
  var BOARD_REFRESH_MS = 60000;
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

  /* Notes and the board ride along inside the runtime the renderer already
     receives; the runtime's notesVersion and boardVersion tell us when
     either actually changed. */
  function withNotes(value) {
    if (value) {
      value.notes = notes;
      value.board = board;
    }
    return value;
  }

  function refreshBoard(version) {
    if (boardPending) return Promise.resolve();
    boardPending = true;
    return fetchJson('/api/v1/board').then(function (result) {
      boardPending = false;
      board = result;
      boardVersion = typeof result.version === 'number' ? result.version : version;
      boardFetchedAt = Date.now();
      renderer.update(layout, withNotes(runtime));
    }).then(null, function () { boardPending = false; });
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
      /* Hold the first reveal until the notes and the board are in so
         nothing pops in late. */
      var pending = [];
      if (next.notesVersion !== notesVersion) pending.push(refreshNotes(next.notesVersion));
      if (next.boardVersion !== boardVersion || Date.now() - boardFetchedAt >= BOARD_REFRESH_MS) {
        pending.push(refreshBoard(next.boardVersion));
      }
      return Promise.all(pending).then(reveal);
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