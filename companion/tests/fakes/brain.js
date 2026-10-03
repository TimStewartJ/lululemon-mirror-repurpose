import { BrainError } from "../../src/brain.js";
import { runTool } from "../../src/tools.js";

/**
 * A brain that follows a script: for each turn a list of tool calls and a
 * final text. It runs the real tools, so a test sees what a model making
 * those calls would cause.
 *
 * A turn in the script may have:
 *   calls   [{ tool, args }], where args may be a function of the results so far
 *   text    the final message, or a function of the tool results
 *   fail    an error to throw in place of an answer
 *   hangs   true to never answer, until the timeout or the signal
 *   before  a function to await before anything else
 *
 * @param {import("../../src/clock.js").Clock} clock
 * @param {object[]} [script]
 */
export function scriptedBrain(clock, script = []) {
  const brain = {
    /** What each run was given and what its tools answered. */
    runs: [],
    prepared: [],
    ended: 0,
    ready: true,
    detail: "",
    script,

    async start() {},
    async stop() {},
    health: () => ({ ready: brain.ready, detail: brain.detail }),
    prepare: (run) => brain.prepared.push(run.fresh),
    async endConversation() {
      brain.ended += 1;
    },

    async run(run) {
      if (!brain.ready) throw new BrainError("not-ready", brain.detail);
      const turn = brain.script.shift() ?? { text: "Done." };
      const results = [];
      brain.runs.push({
        session: run.session,
        fresh: run.fresh ?? false,
        system: run.system,
        prompt: run.prompt,
        tools: run.tools.map((tool) => tool.name),
        timeoutMs: run.timeoutMs,
        results,
      });
      if (turn.before) await turn.before();
      if (turn.hangs) {
        return new Promise((resolve, reject) => {
          clock.setTimeout(() => reject(new BrainError("timeout", "The scripted model never answered.")), run.timeoutMs);
          run.signal?.addEventListener("abort", () => reject(new BrainError("aborted", "Stopped for a person.")));
        });
      }
      if (turn.fail) throw turn.fail;
      for (const call of turn.calls ?? []) {
        const tool = run.tools.find((candidate) => candidate.name === call.tool);
        if (!tool) throw new Error(`The script calls ${call.tool}, which this run was not given.`);
        const result = await runTool(tool, typeof call.args === "function" ? call.args(results) : call.args, run.turn);
        results.push(result);
        if (tool.endsTurn && !result.error) return { text: "" };
      }
      return { text: typeof turn.text === "function" ? turn.text(results) : turn.text ?? "" };
    },
  };
  return brain;
}
