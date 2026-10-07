/**
 * Builds the health report from what the parts already know. Nothing here
 * asks the model or the mirror, so the report is ready at once.
 *
 * @param {Object} parts
 * @param {string} parts.version
 * @param {string} parts.provider
 * @param {string} parts.model
 * @param {{ health: () => { ready: boolean, detail: string } }} parts.brain
 * @param {{ health: () => { ready: boolean, model: string, device: string, detail: string } }} parts.stt
 * @param {{ health: () => { reachable: boolean, version: string, detail: string } }} parts.mirror
 * @param {{ busy: boolean }} parts.queue
 * @param {{ lastExchange: () => object | null }} parts.activity
 * @param {{ now: () => number }} parts.clock
 * @param {{ calls: () => number } | null} [parts.mcp] The tools for programs on the network, when they are served.
 * @param {boolean} [parts.toolsOnly] True for a companion that serves those tools and nothing else: it is well
 *   when the mirror can be reached, since no model and no speech-to-text were started.
 */
export function createHealth({ version, provider, model, brain, stt, mirror, queue, activity, clock, mcp = null, toolsOnly = false }) {
  const startedAt = clock.now();
  const NOT_RUN = "Not run: this companion serves its tools only.";
  return function health() {
    const brainNow = toolsOnly ? { ready: false, detail: NOT_RUN } : brain.health();
    const sttNow = toolsOnly ? { ...stt.health(), ready: false, detail: NOT_RUN } : stt.health();
    const mirrorNow = mirror.health();
    const last = activity.lastExchange();
    return {
      ok: toolsOnly ? mirrorNow.reachable : brainNow.ready && sttNow.ready && mirrorNow.reachable,
      name: "mirror-companion",
      version,
      provider,
      model,
      brain: { ready: brainNow.ready, detail: brainNow.detail },
      stt: { ready: sttNow.ready, model: sttNow.model, device: sttNow.device, detail: sttNow.detail },
      mirror: { reachable: mirrorNow.reachable, version: mirrorNow.version, detail: mirrorNow.detail },
      mcp: { on: Boolean(mcp), toolsOnly, calls: mcp ? mcp.calls() : 0 },
      busy: queue.busy,
      uptimeSeconds: Math.floor((clock.now() - startedAt) / 1000),
      last: last ? { at: last.at, source: last.source, heard: last.heard, reply: last.reply, ms: last.ms } : null,
    };
  };
}
