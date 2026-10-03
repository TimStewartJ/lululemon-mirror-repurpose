const settle = () => new Promise((resolve) => setImmediate(resolve));

/**
 * A clock that only moves when a test moves it, so nothing has to sleep.
 * It starts on Saturday 3 October 2026 at 07:12 in Los Angeles.
 */
export function fakeClock(start = Date.UTC(2026, 9, 3, 14, 12, 0)) {
  let now = start;
  let nextId = 1;
  const timers = new Map();
  return {
    now: () => now,
    setTimeout(run, ms) {
      const id = nextId++;
      timers.set(id, { at: now + ms, run });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    /** Moves time forward and runs the timers that fall due on the way, in order. */
    async advance(ms) {
      const target = now + ms;
      for (;;) {
        await settle();
        const due = [...timers.entries()].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = Math.max(now, due[1].at);
        due[1].run();
      }
      now = target;
      await settle();
    },
    pending: () => timers.size,
  };
}

/** Waits, in real time, until something that depends on real I/O has happened. */
export async function until(condition, what = "the expected state") {
  const deadline = Date.now() + 5000;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error(`Timed out waiting for ${what}.`);
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}
