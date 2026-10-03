/**
 * Makes sure the agent does one thing at a time. People's requests wait in
 * line, at most two of them in the house at once. Runs the companion starts
 * by itself step aside: they do not begin while a person is being served or
 * waiting, and one that is under way is told to stop when a person arrives.
 */
export function createQueue() {
  let tail = Promise.resolve();
  let running = false;
  let people = 0;
  /** @type {AbortController | null} */
  let proactive = null;

  /** Runs the work when everything before it has finished. */
  async function turn(work) {
    const before = tail;
    let release;
    tail = new Promise((resolve) => (release = resolve));
    await before;
    running = true;
    try {
      return await work();
    } finally {
      running = false;
      release();
    }
  }

  return {
    /** True while anything runs or a person waits. */
    get busy() {
      return running || people > 0;
    },

    /** True while an agent run is under way. */
    get running() {
      return running;
    },

    /**
     * A person's request comes in.
     *
     * @returns {(() => void) | null} what to call when it has been answered, or null when two are already in
     */
    enter() {
      if (people >= 2) return null;
      people += 1;
      proactive?.abort();
      let left = false;
      return () => {
        if (left) return;
        left = true;
        people -= 1;
      };
    },

    turn,

    /**
     * A run the companion starts by itself.
     *
     * @param {(signal: AbortSignal) => Promise<T>} work
     * @returns {Promise<{ skipped: true } | { skipped: false, value: T }>}
     * @template T
     */
    async proactive(work) {
      if (running || people > 0 || proactive) return { skipped: true };
      const controller = new AbortController();
      proactive = controller;
      try {
        return { skipped: false, value: await turn(() => work(controller.signal)) };
      } finally {
        proactive = null;
      }
    },
  };
}
