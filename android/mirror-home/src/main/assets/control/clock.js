/* Works out a time zone's UTC offset and its upcoming daylight-saving changes
   in the browser, which has current time-zone rules. The Mirror's firmware
   does not, so the controls send it this list. Also loaded by Node in tests. */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.MirrorClock = factory();
  }
}(typeof window !== 'undefined' ? window : this, function () {
  'use strict';

  var MINUTE = 60000;
  var DAY = 86400000;
  var HORIZON_DAYS = 3653;
  /* Real changes are weeks apart; a coarse scan keeps this fast on phones. */
  var SCAN_STEP = 4 * DAY;
  var MAX_CHANGES = 64;
  var COMPARED_CHANGES = 4;

  function browserZone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    } catch (error) {
      return 'UTC';
    }
  }

  /* Returns a function giving the zone's offset, in minutes east of UTC, at a
     time in epoch milliseconds. Throws when the browser cannot resolve the zone. */
  function offsetReader(zone) {
    if (zone === browserZone()) {
      return function (time) { return -new Date(time).getTimezoneOffset(); };
    }
    var formatter = new Intl.DateTimeFormat('en-US', {
      timeZone: zone,
      hour12: false,
      year: 'numeric',
      month: 'numeric',
      day: 'numeric',
      hour: 'numeric',
      minute: 'numeric',
      second: 'numeric'
    });
    if (typeof formatter.formatToParts !== 'function') {
      throw new Error('This browser cannot look up other time zones');
    }
    return function (time) {
      var fields = {};
      formatter.formatToParts(new Date(time)).forEach(function (part) {
        fields[part.type] = Number(part.value);
      });
      /* Some engines print midnight as hour 24. */
      var wall = Date.UTC(fields.year, fields.month - 1, fields.day,
        fields.hour % 24, fields.minute, fields.second);
      return Math.round((wall - time) / MINUTE);
    };
  }

  /* The zone's offset now and each change over the coming years:
     { utcOffsetMinutes, utcOffsetChanges: [{ at, utcOffsetMinutes }] }. */
  function describe(zone, now) {
    var offsetAt = offsetReader(zone);
    var start = Math.floor((now == null ? Date.now() : now) / MINUTE) * MINUTE;
    var end = start + HORIZON_DAYS * DAY;
    var changes = [];
    var cursor = start;
    var current = offsetAt(start);
    while (cursor < end && changes.length < MAX_CHANGES) {
      var probe = Math.min(cursor + SCAN_STEP, end);
      if (offsetAt(probe) === current) {
        cursor = probe;
        continue;
      }
      var low = cursor;
      var high = probe;
      while (high - low > MINUTE) {
        var middle = low + Math.floor((high - low) / (2 * MINUTE)) * MINUTE;
        if (offsetAt(middle) === current) low = middle; else high = middle;
      }
      current = offsetAt(high);
      changes.push({ at: high, utcOffsetMinutes: current });
      cursor = high;
    }
    return { utcOffsetMinutes: offsetAt(start), utcOffsetChanges: changes };
  }

  /* Whether the Mirror's saved clock already agrees with the browser's view,
     comparing the offset in force and the next few changes. */
  function matches(preferences, clock) {
    if (Number(preferences.utcOffsetMinutes) !== clock.utcOffsetMinutes) return false;
    var known = preferences.utcOffsetChanges || [];
    var wanted = clock.utcOffsetChanges;
    if (wanted.length < COMPARED_CHANGES) {
      if (known.length !== wanted.length) return false;
    } else if (known.length < COMPARED_CHANGES) {
      return false;
    }
    var count = Math.min(COMPARED_CHANGES, wanted.length);
    for (var index = 0; index < count; index++) {
      if (Number(known[index].at) !== wanted[index].at
          || Number(known[index].utcOffsetMinutes) !== wanted[index].utcOffsetMinutes) {
        return false;
      }
    }
    return true;
  }

  return {
    browserZone: browserZone,
    describe: describe,
    matches: matches
  };
}));
