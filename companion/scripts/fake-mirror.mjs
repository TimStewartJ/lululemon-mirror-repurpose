// Runs the fake mirror of the tests as a program of its own, so that the
// companion can be tried from end to end without a real mirror: pair with it,
// start the companion, and send recordings or typed words.
//
//   node scripts/fake-mirror.mjs [--port 28787] [--offset MINUTES] [--zone NAME]
//
// It prints every caption it is asked to show and every change made to it.

import { parseArgs } from "node:util";
import { localIso } from "../src/time.js";
import { PAIRING_CODE, startFakeMirror } from "../tests/fakes/mirror.js";

const { values: options } = parseArgs({
  options: {
    port: { type: "string", default: "28787" },
    offset: { type: "string", default: "-420" },
    zone: { type: "string", default: "America/Los_Angeles" },
  },
});
const offset = Number(options.offset);

const mirror = await startFakeMirror({
  port: Number(options.port),
  timeZone: options.zone,
  utcOffsetMinutes: offset,
  onRequest(request) {
    if (request.method === "GET" || request.path.endsWith("/validate")) return;
    const stamp = localIso(Date.now(), offset).slice(11, 19);
    if (request.path === "/api/v1/assistant/say") {
      console.log(`${stamp} caption (${request.body.kind ?? "reply"}): ${request.body.text}`);
    } else if (request.path === "/api/v1/dashboard/layout") {
      const now = (id) => request.body.widgets.find((widget) => widget.id === id);
      const before = (id) => mirror.widget(id);
      const changed = request.body.widgets
        .filter((widget) => JSON.stringify(before(widget.id)) !== JSON.stringify(now(widget.id)))
        .map(
          (widget) =>
            `${widget.id} ${widget.visible ? "shown" : "hidden"} at x ${widget.x}, y ${widget.y}, w ${widget.w}, h ${widget.h}` +
            (widget.type === "board" ? `, listing ${widget.show}, size ${widget.size}` : ""),
        );
      const mode = request.body.background.mode === mirror.state.layout.background.mode ? [] : [`background ${request.body.background.mode}`];
      console.log(`${stamp} layout: ${[...changed, ...mode].join("; ") || "written unchanged"}`);
    } else {
      const due = typeof request.body?.due === "number" ? { ...request.body, due: localIso(request.body.due, offset) } : request.body;
      console.log(`${stamp} ${request.method} ${request.path}${due ? ` ${JSON.stringify(due)}` : ""}`);
    }
  },
});

console.log(`A fake mirror listens on ${mirror.host}:${mirror.port}. Its zone is ${options.zone}, and its clock reads ${localIso(Date.now(), offset)}.`);
console.log(`Pair with it: node src/cli.js pair --host ${mirror.host} --port ${mirror.port} --code ${PAIRING_CODE}`);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, async () => {
    await mirror.close();
    process.exit(0);
  });
}
