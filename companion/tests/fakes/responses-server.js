import http from "node:http";
import { WebSocketServer } from "ws";

/**
 * A stand-in for a service that speaks the Responses API, over HTTP and over
 * a WebSocket, and for the two lookups GitHub answers about a Copilot
 * account. `answers` holds what the model says to each request, in order:
 * a string (its words), { call: [name, args] } for a tool call, { error }
 * for a refusal, or a function of the request that returns one of these.
 *
 * @param {Object} [options]
 * @param {boolean} [options.sockets] False to turn the upgrade to a WebSocket down.
 * @param {string[]} [options.overSocket] The models that are said to be reachable over a socket.
 */
export async function startResponsesServer({ sockets = true, overSocket = ["gpt-6-luna"] } = {}) {
  const fake = {
    /** Every request for an answer: how it came, its headers and its body. */
    requests: [],
    lookups: [],
    socketsOpened: 0,
    answers: [],
    /** Set to stop in the middle of an answer and wait until it is called. */
    holdAfterFirstEvent: null,
    live: new Set(),
  };

  function eventsFor(body) {
    const next = fake.answers.shift() ?? "Done.";
    const said = typeof next === "function" ? next(body) : next;
    const id = `resp_${fake.requests.length}`;
    if (said?.error) return [{ type: "error", status: said.status ?? 400, error: { type: "invalid_request_error", code: said.code ?? "bad_request", message: said.error } }];
    const usage = { input_tokens: 120, output_tokens: 7, total_tokens: 127, input_tokens_details: { cached_tokens: 100 }, output_tokens_details: { reasoning_tokens: 0 } };
    const created = { type: "response.created", response: { id, object: "response", status: "in_progress", model: body.model, output: [] } };
    if (said?.call) {
      const [name, args] = said.call;
      const item = { id: `fc_${id}`, type: "function_call", call_id: `call_${id}`, name, arguments: JSON.stringify(args), status: "completed" };
      return [
        created,
        { type: "response.output_item.added", output_index: 0, item: { ...item, arguments: "", status: "in_progress" } },
        { type: "response.function_call_arguments.delta", item_id: item.id, output_index: 0, delta: item.arguments },
        { type: "response.function_call_arguments.done", item_id: item.id, output_index: 0, arguments: item.arguments },
        { type: "response.output_item.done", output_index: 0, item },
        { type: "response.completed", response: { id, object: "response", status: "completed", model: body.model, output: [item], usage } },
      ];
    }
    const text = String(said);
    const item = { id: `msg_${id}`, type: "message", role: "assistant", status: "completed", content: [{ type: "output_text", text, annotations: [] }] };
    return [
      created,
      { type: "response.output_item.added", output_index: 0, item: { ...item, status: "in_progress", content: [] } },
      { type: "response.content_part.added", item_id: item.id, output_index: 0, content_index: 0, part: { type: "output_text", text: "", annotations: [] } },
      { type: "response.output_text.delta", item_id: item.id, output_index: 0, content_index: 0, delta: text },
      { type: "response.output_text.done", item_id: item.id, output_index: 0, content_index: 0, text },
      { type: "response.content_part.done", item_id: item.id, output_index: 0, content_index: 0, part: item.content[0] },
      { type: "response.output_item.done", output_index: 0, item },
      { type: "response.completed", response: { id, object: "response", status: "completed", model: body.model, output: [item], usage } },
    ];
  }

  /** Sends the events one by one; stops after the first when a test holds the answer. */
  async function send(events, write) {
    for (const [at, event] of events.entries()) {
      write(event);
      if (at === 0 && fake.holdAfterFirstEvent) await new Promise((resolve) => (fake.holdAfterFirstEvent = resolve));
    }
  }

  const server = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", async () => {
      const json = (status, body) => {
        response.writeHead(status, { "Content-Type": "application/json" });
        response.end(JSON.stringify(body));
      };
      if (request.method === "GET" && request.url === "/copilot_internal/user") {
        fake.lookups.push({ path: request.url, headers: request.headers });
        if (fake.refuseLookups) return json(fake.refuseLookups, { message: "Bad credentials" });
        return json(200, { login: "someone", copilot_plan: "individual", endpoints: { api: fake.url } });
      }
      if (request.method === "GET" && request.url === "/models") {
        fake.lookups.push({ path: request.url, headers: request.headers });
        return json(200, {
          data: [
            { id: "gpt-6-luna", model_picker_enabled: true, policy: { state: "enabled" }, supported_endpoints: ["/responses", ...(overSocket.includes("gpt-6-luna") ? ["ws:/responses"] : [])] },
            { id: "gpt-5-mini", model_picker_enabled: true, supported_endpoints: ["/chat/completions", "/responses"] },
            { id: "claude-haiku-4.5", model_picker_enabled: true, policy: { state: "disabled" } },
            { id: "gpt-6-sol", model_picker_enabled: false, policy: { state: "enabled" } },
            { id: "a-model-pi-does-not-know", model_picker_enabled: true },
          ],
        });
      }
      if (request.method === "POST" && request.url === "/responses") {
        const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        fake.requests.push({ over: "http", headers: request.headers, body });
        const events = eventsFor(body);
        if (events[0].type === "error") return json(events[0].status, { error: events[0].error });
        response.writeHead(200, { "Content-Type": "text/event-stream" });
        await send(events, (event) => response.write(`event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`));
        return response.end();
      }
      if (request.method === "POST" && request.url === "/chat/completions") {
        const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        fake.requests.push({ over: "http", headers: request.headers, body });
        const text = String(fake.answers.shift() ?? "Done.");
        response.writeHead(200, { "Content-Type": "text/event-stream" });
        const chunk = (delta, finish = null) => ({ id: "chatcmpl-1", object: "chat.completion.chunk", created: 1, model: body.model, choices: [{ index: 0, delta, finish_reason: finish }] });
        response.write(`data: ${JSON.stringify(chunk({ role: "assistant", content: text }))}\n\n`);
        response.write(`data: ${JSON.stringify(chunk({}, "stop"))}\n\n`);
        response.write(`data: ${JSON.stringify({ ...chunk({}), choices: [], usage: { prompt_tokens: 20, completion_tokens: 3, total_tokens: 23 } })}\n\n`);
        return response.end("data: [DONE]\n\n");
      }
      json(404, { error: { message: "No such path." } });
    });
  });

  const socketServer = new WebSocketServer({ noServer: true });
  server.on("upgrade", (request, socket, head) => {
    if (!sockets || fake.refuseSockets || request.url !== "/responses") {
      socket.write("HTTP/1.1 404 Not Found\r\n\r\n");
      return socket.destroy();
    }
    socketServer.handleUpgrade(request, socket, head, (ws) => {
      fake.socketsOpened += 1;
      fake.live.add(ws);
      ws.on("close", () => fake.live.delete(ws));
      ws.on("message", async (data) => {
        const body = JSON.parse(data.toString("utf8"));
        fake.requests.push({ over: "socket", headers: request.headers, body });
        if (fake.silent) return;
        if (body.type !== "response.create") return ws.send(JSON.stringify({ type: "error", status: 400, error: { code: "unknown_message", message: "Not a response.create message." } }));
        await send(eventsFor(body), (event) => ws.readyState === ws.OPEN && ws.send(JSON.stringify(event)));
      });
    });
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  fake.url = `http://127.0.0.1:${server.address().port}`;
  /** A fetch that takes what is asked of GitHub to this server. */
  fake.fetch = (url, init) => fetch(String(url).replace("https://api.github.com", fake.url), init);
  fake.close = async () => {
    for (const ws of fake.live) ws.terminate();
    socketServer.close();
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(resolve));
  };
  return fake;
}
