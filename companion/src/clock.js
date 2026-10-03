/**
 * The clock everything time-dependent goes through, so that tests can supply
 * their own and never sleep.
 *
 * @typedef {Object} Clock
 * @property {() => number} now Milliseconds since 1970.
 * @property {(run: () => void, ms: number) => unknown} setTimeout
 * @property {(handle: unknown) => void} clearTimeout
 */

/** @type {Clock} */
export const systemClock = {
  now: () => Date.now(),
  setTimeout: (run, ms) => {
    const handle = setTimeout(run, ms);
    // A pending timer must not keep the process alive after "serve" is told to stop.
    handle.unref?.();
    return handle;
  },
  clearTimeout: (handle) => clearTimeout(handle),
};
