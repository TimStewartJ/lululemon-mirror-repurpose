import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createModels, createProvider } from "@earendil-works/pi-ai";
import { anthropicMessagesApi } from "@earendil-works/pi-ai/api/anthropic-messages.lazy";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import { openAIResponsesApi } from "@earendil-works/pi-ai/api/openai-responses.lazy";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import { githubCopilotProvider } from "@earendil-works/pi-ai/providers/github-copilot";
import { responsesOverSocket } from "./responses-socket.js";

// Which model answers, and whose account pays for it. Everything here is
// Pi's (@earendil-works/pi-ai): its providers, its sign-ins and its model
// lists. Two things are added: GitHub Copilot with the sign-in the Copilot
// CLI already has, and a server of one's own that Pi has no provider for.

/** The name, in the config, of GitHub Copilot with the Copilot CLI's sign-in. */
export const COPILOT_CLI = "copilot-cli";

const COPILOT_ID = "github-copilot";
const APIS = {
  "openai-completions": openAICompletionsApi,
  "openai-responses": openAIResponsesApi,
  "anthropic-messages": anthropicMessagesApi,
};
const LOOK_UP_AGAIN_MS = 30 * 60_000;
const LOOK_UP_WITHIN_MS = 10_000;

/**
 * The sign-ins the "login" command stores, in a file only its owner can
 * read. The file has the form of Pi's own auth.json: one entry for each
 * provider.
 *
 * A write reads the file again first and replaces one entry, under a lock
 * file, so that the running companion renewing a sign-in and a "login" in
 * a terminal do not undo one another.
 *
 * @param {string} file
 * @returns {import("@earendil-works/pi-ai").CredentialStore}
 */
export function credentialFile(file) {
  let chain = Promise.resolve();
  const inTurn = (task) => {
    const next = chain.then(task);
    chain = next.catch(() => {});
    return next;
  };

  function readAll() {
    let text;
    try {
      text = fs.readFileSync(file, "utf8");
    } catch (error) {
      if (error.code === "ENOENT") return {};
      throw new Error(`The sign-ins in ${file} could not be read: ${error.message}`);
    }
    try {
      const all = JSON.parse(text);
      return all && typeof all === "object" && !Array.isArray(all) ? all : {};
    } catch (error) {
      throw new Error(`The sign-ins in ${file} are not valid JSON: ${error.message}`);
    }
  }

  function writeAll(all) {
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    const draft = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(draft, JSON.stringify(all, null, 2) + "\n", { mode: 0o600 });
    fs.renameSync(draft, file);
  }

  async function locked(task) {
    const lock = `${file}.lock`;
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    for (let waited = 0; ; waited += 50) {
      try {
        fs.closeSync(fs.openSync(lock, "wx", 0o600));
        break;
      } catch (error) {
        if (error.code !== "EEXIST") throw error;
        // A lock left behind by a process that died: renewing a sign-in takes 15 s at most.
        const age = Date.now() - (fs.statSync(lock, { throwIfNoEntry: false })?.mtimeMs ?? Date.now());
        if (age > 30_000 || waited > 40_000) fs.rmSync(lock, { force: true });
        else await new Promise((resolve) => setTimeout(resolve, 50));
      }
    }
    try {
      return await task();
    } finally {
      fs.rmSync(lock, { force: true });
    }
  }

  return {
    async read(providerId) {
      return readAll()[providerId];
    },
    async list() {
      return Object.entries(readAll()).map(([providerId, credential]) => ({ providerId, type: credential.type }));
    },
    modify(providerId, change) {
      return inTurn(() =>
        locked(async () => {
          const current = readAll()[providerId];
          const next = await change(current);
          if (next === undefined) return current;
          writeAll({ ...readAll(), [providerId]: next });
          return next;
        }),
      );
    },
    delete(providerId) {
      return inTurn(() =>
        locked(async () => {
          const all = readAll();
          if (!(providerId in all)) return;
          delete all[providerId];
          writeAll(all);
        }),
      );
    },
  };
}

/**
 * Finds the GitHub sign-in the Copilot CLI uses: the token in
 * COPILOT_GITHUB_TOKEN, as the CLI itself takes it first, or the one the
 * CLI wrote to its config. It writes it there only where the machine has
 * no keychain, which is the usual case on a server.
 *
 * @returns {{ token: string, host: string, source: string } | { missing: string }}
 */
export function findCopilotCliSignIn({ env = process.env, home = os.homedir() } = {}) {
  if (env.COPILOT_GITHUB_TOKEN) return { token: env.COPILOT_GITHUB_TOKEN, host: "github.com", source: "COPILOT_GITHUB_TOKEN" };
  const file = path.join(env.COPILOT_HOME || path.join(home, ".copilot"), "config.json");
  let config;
  try {
    // The CLI heads its config with comment lines.
    const text = fs.readFileSync(file, "utf8").split("\n").filter((line) => !line.trimStart().startsWith("//")).join("\n");
    config = JSON.parse(text);
  } catch (error) {
    if (error.code === "ENOENT") return { missing: `The Copilot CLI has no config at ${file}.` };
    return { missing: `The Copilot CLI's config at ${file} could not be read: ${error.message}.` };
  }
  const stored = Object.entries(config.authTokens ?? {})
    .map(([key, value]) => ({ key, token: typeof value === "string" ? value : value?.token }))
    .filter((entry) => typeof entry.token === "string" && entry.token !== "");
  if (stored.length === 0) {
    return {
      missing: (config.loggedInUsers ?? []).length > 0
        ? `The Copilot CLI is signed in, but keeps the sign-in in this machine's keychain and not in ${file}.`
        : `Nobody is signed in to the Copilot CLI (${file}).`,
    };
  }
  const last = config.lastLoggedInUser;
  const wanted = last?.host && last?.login ? `${last.host}:${last.login}` : null;
  const entry = stored.find(({ key }) => key === wanted) ?? stored.find(({ key }) => wanted && key.startsWith(`${wanted}:`)) ?? stored[0];
  const host = /^https?:\/\/([^:/]+)/.exec(entry.key)?.[1] ?? "github.com";
  return { token: entry.token, host, source: `the Copilot CLI's sign-in (${file})` };
}

/**
 * GitHub Copilot, reached with the sign-in the Copilot CLI has, so that
 * nobody has to sign in a second time. Pi's own Copilot provider signs in
 * by itself and presents itself as VS Code; with the CLI's token GitHub
 * offers the CLI's models only to requests that carry the CLI's
 * integration id, so that is what this one sends. The models, and how each
 * is spoken to, are Pi's. One thing is done as the CLI does it and not as
 * Pi does: where a model can be asked over a socket that stays open, it is,
 * because each call then comes back sooner and does not grow slower as the
 * conversation grows (see responses-socket.js).
 * MIRROR_COMPANION_NO_WEBSOCKET=1 switches that off.
 *
 * @param {Object} [options]
 * @param {Record<string, string | undefined>} [options.env]
 * @param {string} [options.home]
 * @param {typeof fetch} [options.fetch]
 * @param {string} [options.userAgent]
 * @param {(event: string, fields?: object) => void} [options.log]
 * @param {() => number} [options.now]
 * @returns {{ provider: import("@earendil-works/pi-ai").Provider, close: () => void }}
 */
export function copilotCliProvider({ env = process.env, home = os.homedir(), fetch = globalThis.fetch, userAgent = "mirror-companion", log = () => {}, now = Date.now } = {}) {
  const headers = { "User-Agent": userAgent, "Copilot-Integration-Id": "copilot-developer-cli" };
  /**
   * What GitHub said of the account the token belongs to.
   * @type {{ token: string, baseUrl: string, offered: Set<string>, overSocket: Set<string>, at: number } | null}
   */
  let account = null;
  let looking = null;
  // Who began a request is a header over HTTP; over a socket the service reads it from the message.
  const sockets = env.MIRROR_COMPANION_NO_WEBSOCKET ? null : responsesOverSocket({ log, saidInTheMessage: { "x-initiator": "initiator" } });
  const responses = openAIResponsesApi();
  const withSocket = (model, options) => (sockets && account?.overSocket.has(model.id) ? { ...options, fetch: sockets.fetch } : options);

  async function getJson(url, authorization, signal) {
    const response = await fetch(url, {
      headers: { Accept: "application/json", Authorization: authorization, ...headers },
      signal: AbortSignal.any([signal, AbortSignal.timeout(LOOK_UP_WITHIN_MS)]),
    });
    if (!response.ok) {
      const said = (await response.text().catch(() => "")).replace(/\s+/g, " ").slice(0, 120);
      const hint = response.status === 401 ? " Sign in to the Copilot CLI again." : response.status === 403 || response.status === 404 ? " The account may have no Copilot." : "";
      throw new Error(`GitHub answered ${response.status} to the Copilot CLI's sign-in${said ? ` (${said})` : ""}.${hint}`);
    }
    return response.json();
  }

  async function lookUp({ token, host }, signal) {
    const github = host === "github.com" ? "https://api.github.com" : `https://api.${host}`;
    const user = await getJson(`${github}/copilot_internal/user`, `token ${token}`, signal);
    const baseUrl = typeof user?.endpoints?.api === "string" ? user.endpoints.api : "https://api.githubcopilot.com";
    const list = await getJson(`${baseUrl}/models`, `Bearer ${token}`, signal);
    const all = Array.isArray(list?.data) ? list.data : [];
    const offered = new Set(
      all.filter((model) => model?.model_picker_enabled === true && model?.policy?.state !== "disabled").map((model) => model.id),
    );
    const overSocket = new Set(
      all.filter((model) => Array.isArray(model?.supported_endpoints) && model.supported_endpoints.includes("ws:/responses")).map((model) => model.id),
    );
    account = { token, baseUrl, offered, overSocket, at: now() };
    return account;
  }

  function lookUpOnce(found, signal) {
    looking ??= lookUp(found, signal).finally(() => {
      looking = null;
    });
    return looking;
  }

  const provider = createProvider({
    id: COPILOT_ID,
    name: "GitHub Copilot, with the Copilot CLI's sign-in",
    baseUrl: "https://api.githubcopilot.com",
    auth: {
      apiKey: {
        name: "The Copilot CLI's sign-in",
        async resolve({ signal }) {
          const found = findCopilotCliSignIn({ env, home });
          if (!found.token) return undefined;
          let known = account?.token === found.token ? account : null;
          if (!known) known = await lookUpOnce(found, signal);
          // What the account is offered can change; it is looked up again now and then, behind the request.
          else if (now() - known.at > LOOK_UP_AGAIN_MS) lookUpOnce(found, AbortSignal.timeout(LOOK_UP_WITHIN_MS)).catch(() => {});
          return { auth: { apiKey: found.token, baseUrl: known.baseUrl }, source: found.source };
        },
      },
    },
    models: githubCopilotProvider().getModels().map((model) => ({ ...model, headers })),
    filterModels: (models) => (account ? models.filter((model) => account.offered.has(model.id)) : models),
    api: {
      "anthropic-messages": anthropicMessagesApi(),
      "openai-completions": openAICompletionsApi(),
      "openai-responses": {
        stream: (model, context, options) => responses.stream(model, context, withSocket(model, options)),
        streamSimple: (model, context, options) => responses.streamSimple(model, context, withSocket(model, options)),
      },
    },
  });
  return { provider, close: () => sockets?.close() };
}

/**
 * A server that Pi has no provider for, such as Ollama, LM Studio, vLLM or
 * a proxy: one model at an address that speaks one of the three APIs.
 *
 * @param {string} id The name given to it in the config.
 * @param {string} modelId
 * @param {NonNullable<import("./config.js").Config["endpoint"]>} endpoint
 * @returns {import("@earendil-works/pi-ai").Provider}
 */
export function endpointProvider(id, modelId, endpoint) {
  const model = {
    id: modelId,
    name: modelId,
    api: endpoint.api,
    provider: id,
    baseUrl: endpoint.baseUrl,
    reasoning: endpoint.reasoning,
    input: endpoint.images ? ["text", "image"] : ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: endpoint.contextWindow,
    maxTokens: endpoint.maxTokens,
    // Servers of this kind seldom know the newer roles and fields of the hosted APIs.
    ...(endpoint.api === "openai-completions"
      ? { compat: { supportsDeveloperRole: false, supportsStore: false, supportsReasoningEffort: endpoint.reasoning, ...endpoint.compat } }
      : Object.keys(endpoint.compat).length > 0
        ? { compat: endpoint.compat }
        : {}),
  };
  return createProvider({
    id,
    name: `${id} at ${endpoint.baseUrl}`,
    baseUrl: endpoint.baseUrl,
    auth: {
      apiKey: {
        name: `The key for ${id}`,
        async login(interaction) {
          const key = await interaction.prompt({ type: "secret", message: `Enter the key for ${id}` });
          return { type: "api_key", key };
        },
        async resolve({ ctx, credential }) {
          if (credential?.key) return { auth: { apiKey: credential.key }, source: "a stored key" };
          // A server that asks for no key still gets one: the client libraries refuse to send a request without.
          if (!endpoint.apiKeyEnv) return { auth: { apiKey: "none" }, source: "no key needed" };
          const key = await ctx.env(endpoint.apiKeyEnv);
          return key ? { auth: { apiKey: key }, source: endpoint.apiKeyEnv } : undefined;
        },
      },
    },
    models: [model],
    api: APIS[endpoint.api](),
  });
}

/** Pi's own providers, with the sign-ins the "login" command keeps and the environment. */
export function piModels({ authFile, env = process.env, home = os.homedir() }) {
  return builtinModels({
    credentials: credentialFile(authFile),
    authContext: {
      env: async (name) => env[name] || undefined,
      fileExists: async (file) => fs.existsSync(file.startsWith("~") ? path.join(home, file.slice(1)) : file),
    },
  });
}

/**
 * The providers the config's choice opens, and the id Pi knows the chosen
 * one by.
 *
 * @param {Object} options
 * @param {string} options.provider As the config names it: a provider the "providers" command lists, or any other
 *   name when an endpoint is given.
 * @param {string} options.model
 * @param {import("./config.js").Config["endpoint"]} [options.endpoint]
 * @param {string} options.authFile Where the "login" command keeps its sign-ins.
 * @param {Record<string, string | undefined>} [options.env]
 * @param {string} [options.home]
 * @param {typeof fetch} [options.fetch]
 * @param {string} [options.userAgent]
 * @param {(event: string, fields?: object) => void} [options.log]
 * @returns {{ models: import("@earendil-works/pi-ai").MutableModels, providerId: string, signIn: string, close: () => void }}
 *   `signIn` says how to sign in, for when nobody is; `close` ends what the providers keep open.
 */
export function openModels({ provider, model, endpoint = null, authFile, env = process.env, home = os.homedir(), fetch = globalThis.fetch, userAgent, log }) {
  const login = `node src/cli.js login ${provider}`;
  if (provider === COPILOT_CLI) {
    if (endpoint) throw new Error(`"endpoint" is for a server of your own. Give it another name than "${COPILOT_CLI}" in "provider".`);
    const models = createModels();
    const copilot = copilotCliProvider({ env, home, fetch, userAgent, log });
    models.setProvider(copilot.provider);
    const found = findCopilotCliSignIn({ env, home });
    return {
      models,
      providerId: COPILOT_ID,
      close: copilot.close,
      signIn: (
        `${found.missing ?? ""} Either sign in with the Copilot CLI as this user ("copilot", then "/login"; the companion finds ` +
        `the sign-in where the machine has no keychain, as on most servers), or put a token in COPILOT_GITHUB_TOKEN, ` +
        `or set "provider" to "${COPILOT_ID}" and sign in with: node src/cli.js login ${COPILOT_ID}`
      ).trim(),
    };
  }
  const models = piModels({ authFile, env, home });
  if (endpoint) {
    if (models.getProvider(provider)) {
      throw new Error(`"endpoint" is for a server Pi has no provider for, and "${provider}" is one of Pi's. Leave "endpoint" out, or give the server another name in "provider".`);
    }
    models.setProvider(endpointProvider(provider, model, endpoint));
    return {
      models,
      providerId: provider,
      close: () => {},
      signIn: endpoint.apiKeyEnv ? `Put the key in ${endpoint.apiKeyEnv}, or store one with: ${login}` : "",
    };
  }
  const chosen = models.getProvider(provider);
  if (!chosen) {
    throw new Error(
      `There is no provider called "${provider}". Set "provider" in the config to one of: ` +
        `${[COPILOT_CLI, ...models.getProviders().map((known) => known.id)].sort().join(", ")}. For a server of your own, give "endpoint" as well.`,
    );
  }
  return {
    models,
    providerId: provider,
    close: () => {},
    signIn: chosen.auth.oauth
      ? `Sign in with: ${login}`
      : chosen.auth.apiKey?.login
        ? `Store a key with: ${login}  (or put it in the environment variable Pi reads for ${provider})`
        : `Give ${chosen.name} its credentials in the environment the companion runs in.`,
  };
}
