// The Responses API over a WebSocket that is kept open, behind the face of
// fetch. Pi's client for that API asks over HTTP, one request for each call
// to the model, and sends the whole conversation every time. Where the
// service also takes requests over a socket, as GitHub Copilot does, a call
// comes back sooner (measured with gpt-6-luna: 1.5 s over HTTP, 1.2 s over
// the socket), and a call that follows the one before on the same socket
// need only bring what is new, so that it does not grow slower as the
// conversation grows. That is how the Copilot CLI asks, and the forms used
// here are the ones it uses: a "response.create" message for each request,
// "previous_response_id" to go on from the response before, and who began
// the request said in the message and not in a header.
//
// The request Pi built is otherwise sent as it is, and the events that come
// back are handed on as the event stream the HTTP request would have had, so
// Pi notices no difference. Whatever goes wrong before an answer begins
// falls back: from going on after the response before to the whole
// conversation, and from the socket to the plain request.

const ENDS = new Set(["response.completed", "response.failed", "response.incomplete", "error"]);
// What the client library adds for an HTTP request and a socket has no use for.
const NOT_FOR_A_SOCKET = /^(content-type|content-length|accept|accept-encoding|connection|host|x-stainless-.*)$/i;

class SocketFailed extends Error {}

/** Whether an item of a request's input is one the model wrote: Pi sends the model's own answers back with the rest. */
function fromTheModel(item) {
  return item?.type === "reasoning" || item?.type === "function_call" || item?.type === "custom_tool_call" || item?.role === "assistant";
}

const sameSet = (one, other) => one.size === other.size && [...one].every((member) => other.has(member));

/**
 * The request that goes on from the response before, or null when this
 * request is not plainly that one's sequel. It is when everything sent
 * before is sent again unchanged, followed by what the model then wrote,
 * followed by something new that answers every call the model made and no
 * other. Then only the new part needs sending.
 *
 * @param {{ id: string, model: string, sent: string[], calls: Set<string> } | null} last
 * @param {{ model?: string, input?: unknown }} request
 */
export function sequel(last, request) {
  const input = request.input;
  if (!last || last.model !== request.model || !Array.isArray(input) || input.length <= last.sent.length) return null;
  if (last.sent.some((item, at) => JSON.stringify(input[at]) !== item)) return null;
  const rest = input.slice(last.sent.length);
  const written = rest.findIndex((item) => !fromTheModel(item));
  if (written < 0) return null;
  const fresh = rest.slice(written);
  if (fresh.some(fromTheModel)) return null;
  const callsOf = (items, kinds) => new Set(items.filter((item) => kinds.includes(item?.type)).map((item) => item.call_id));
  if (!sameSet(callsOf(rest.slice(0, written), ["function_call", "custom_tool_call"]), last.calls)) return null;
  if (!sameSet(callsOf(fresh, ["function_call_output", "custom_tool_call_output"]), last.calls)) return null;
  return { ...request, input: fresh, previous_response_id: last.id };
}

/**
 * @param {Object} [options]
 * @param {typeof fetch} [options.fetch] The plain request, for everything else and for when a socket fails.
 * @param {typeof WebSocket} [options.WebSocket] Must take headers as its second argument, as Node's does.
 * @param {Record<string, string>} [options.saidInTheMessage] Headers that the service reads from the message when it
 *   comes over a socket, with the name each has there: { "x-initiator": "initiator" }.
 * @param {(event: string, fields?: object) => void} [options.log]
 * @param {number} [options.idleMs] An unused socket is closed after this long.
 * @param {number} [options.oldMs] A socket older than this is not used again: the service ends them after an hour.
 * @param {number} [options.openWithinMs]
 * @param {number} [options.firstWithinMs] The first event of an answer is waited for this long.
 * @param {number} [options.restMs] After a socket failed, plain requests are used for this long.
 * @returns {{ fetch: typeof fetch, close: () => void }}
 */
export function responsesOverSocket({
  fetch = globalThis.fetch,
  WebSocket = globalThis.WebSocket,
  saidInTheMessage = {},
  log = () => {},
  idleMs = 5 * 60_000,
  oldMs = 50 * 60_000,
  openWithinMs = 4000,
  firstWithinMs = 6000,
  restMs = 60_000,
} = {}) {
  /** Open sockets with no request on them, by what they were opened with. @type {Map<string, Set<object>>} */
  const idle = new Map();
  const open = new Set();
  let restUntil = 0;

  function drop(socket) {
    clearTimeout(socket.timer);
    idle.get(socket.key)?.delete(socket);
    open.delete(socket);
    try {
      socket.ws.close();
    } catch {
      // Closed already.
    }
  }

  function release(socket) {
    if (socket.ws.readyState !== WebSocket.OPEN || Date.now() - socket.since > oldMs) return drop(socket);
    if (!idle.has(socket.key)) idle.set(socket.key, new Set());
    idle.get(socket.key).add(socket);
    socket.timer = setTimeout(() => drop(socket), idleMs);
    socket.timer.unref?.();
  }

  function take(key) {
    for (const socket of idle.get(key) ?? []) {
      idle.get(key).delete(socket);
      clearTimeout(socket.timer);
      if (socket.ws.readyState === WebSocket.OPEN) return socket;
      drop(socket);
    }
    return null;
  }

  function connect(url, headers, key, signal) {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(url.replace(/^http/, "ws"), { headers });
      /** `last` is the response the socket brought last, for a request that goes on from it. */
      const socket = { ws, key, since: Date.now(), timer: null, last: null };
      open.add(socket);
      const fail = (error) => {
        clear();
        drop(socket);
        reject(error);
      };
      const timer = setTimeout(() => fail(new SocketFailed(`no socket opened within ${openWithinMs} ms`)), openWithinMs);
      const onOpen = () => {
        clear();
        // A socket the service closes while it lies unused is taken off the list.
        ws.addEventListener("close", () => drop(socket));
        resolve(socket);
      };
      const onError = (event) => fail(new SocketFailed(`the socket could not be opened${event?.message ? `: ${event.message}` : ""}`));
      const onAbort = () => fail(signal.reason ?? new DOMException("The request was aborted.", "AbortError"));
      function clear() {
        clearTimeout(timer);
        ws.removeEventListener("open", onOpen);
        ws.removeEventListener("error", onError);
        ws.removeEventListener("close", onError);
        signal?.removeEventListener("abort", onAbort);
      }
      ws.addEventListener("open", onOpen);
      ws.addEventListener("error", onError);
      ws.addEventListener("close", onError);
      signal?.addEventListener("abort", onAbort, { once: true });
    });
  }

  /**
   * One request on a socket. Resolves with the response once its first event
   * is in. `whole` is the request with the whole conversation; it is what is
   * remembered for the next request, and what is sent after all when the
   * service will not go on from the response before.
   */
  function ask(socket, request, whole, signal) {
    return new Promise((resolve, reject) => {
      const encoder = new TextEncoder();
      const goesOn = request !== whole;
      let begun = false;
      let stream;
      const body = new ReadableStream({
        start: (controller) => {
          stream = controller;
        },
        // The reader went away: the answer is not wanted any more.
        cancel: () => {
          clear();
          drop(socket);
        },
      });
      const giveUp = (error) => {
        clear();
        drop(socket);
        if (begun) stream.error(error);
        else reject(error);
      };
      const timer = setTimeout(() => giveUp(new SocketFailed(`the socket brought no answer within ${firstWithinMs} ms`)), firstWithinMs);
      const onMessage = (message) => {
        let event;
        try {
          event = JSON.parse(message.data);
        } catch {
          return;
        }
        if (!begun) {
          begun = true;
          clearTimeout(timer);
          if (event.type === "error") {
            clear();
            socket.last = null;
            if (goesOn) {
              log("brain.socket_sequel_refused", { detail: String(event.error?.message ?? event.error?.code ?? "no reason given").slice(0, 200) });
              return resolve(ask(socket, whole, whole, signal));
            }
            // Refused before it began: handed on as the HTTP request would have been refused.
            drop(socket);
            return resolve(new Response(JSON.stringify({ error: event.error ?? event }), { status: Number(event.status) || 400, headers: { "content-type": "application/json" } }));
          }
          resolve(new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } }));
        }
        stream.enqueue(encoder.encode(`event: ${event.type}\ndata: ${message.data}\n\n`));
        if (!ENDS.has(event.type)) return;
        clear();
        stream.close();
        if (event.type === "error") return drop(socket);
        const output = Array.isArray(event.response?.output) ? event.response.output : [];
        socket.last =
          event.type === "response.completed" && typeof event.response?.id === "string" && Array.isArray(whole.input)
            ? {
                id: event.response.id,
                model: whole.model,
                sent: whole.input.map((item) => JSON.stringify(item)),
                calls: new Set(output.filter((item) => item?.type === "function_call" || item?.type === "custom_tool_call").map((item) => item.call_id)),
              }
            : null;
        release(socket);
      };
      const onClose = () =>
        giveUp(begun ? new Error("The connection to the model closed before its answer was complete.") : new SocketFailed("the socket closed before it answered"));
      const onAbort = () => giveUp(signal.reason ?? new DOMException("The request was aborted.", "AbortError"));
      function clear() {
        clearTimeout(timer);
        socket.ws.removeEventListener("message", onMessage);
        socket.ws.removeEventListener("close", onClose);
        signal?.removeEventListener("abort", onAbort);
      }
      socket.ws.addEventListener("message", onMessage);
      socket.ws.addEventListener("close", onClose);
      signal?.addEventListener("abort", onAbort, { once: true });
      try {
        socket.ws.send(JSON.stringify(request));
      } catch (error) {
        giveUp(new SocketFailed(`the socket took no request: ${error.message}`));
      }
    });
  }

  return {
    async fetch(input, init) {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : null;
      let asked = null;
      if (url && /\/responses$/.test(url) && init?.method?.toUpperCase() === "POST" && typeof init.body === "string" && Date.now() >= restUntil) {
        try {
          asked = JSON.parse(init.body);
        } catch {
          asked = null;
        }
      }
      if (asked?.stream !== true) return fetch(input, init);
      init.signal?.throwIfAborted();

      const { stream: _stream, background: _background, ...built } = asked;
      const whole = { type: "response.create", ...built };
      // A socket gets its headers once, when it is opened, so it carries only requests whose headers are those.
      // What the service reads from each message instead is said there.
      const headers = {};
      for (const [name, value] of new Headers(init.headers)) {
        if (NOT_FOR_A_SOCKET.test(name)) continue;
        if (saidInTheMessage[name]) whole[saidInTheMessage[name]] = value;
        else headers[name] = value;
      }
      const key = JSON.stringify([url, Object.entries(headers).sort()]);
      try {
        const socket = take(key) ?? (await connect(url, headers, key, init.signal));
        return await ask(socket, sequel(socket.last, whole) ?? whole, whole, init.signal);
      } catch (error) {
        if (!(error instanceof SocketFailed)) throw error;
        restUntil = Date.now() + restMs;
        log("brain.socket_failed", { detail: error.message, plainRequestsForMs: restMs });
        return fetch(input, init);
      }
    },

    close() {
      for (const socket of [...open]) drop(socket);
    },
  };
}
