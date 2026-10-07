import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { z } from "zod";

const clockTime = z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, "must be a time such as 22:30");

const schema = z.object({
  listen: z
    .object({
      host: z.string().min(1).default("0.0.0.0"),
      port: z.number().int().min(1).max(65535).default(8790),
    })
    .prefault({}),
  secret: z.string().min(16, "must be at least 16 characters; \"init\" writes a good one"),
  mirror: z
    .object({
      host: z.string().default(""),
      port: z.number().int().min(1).max(65535).default(8787),
      token: z.string().default(""),
    })
    .prefault({}),
  // Whose model answers: one of Pi's providers (the "providers" command lists them), "copilot-cli" for GitHub
  // Copilot with the sign-in the Copilot CLI has, or any other name together with "endpoint".
  provider: z.string().min(1).default("copilot-cli"),
  model: z.string().min(1).default("gpt-6-luna"),
  // How hard the model thinks before it answers. Measured with gpt-6-luna:
  // with "low" a typical answer takes 2 s, with the model's own default 3.3 s,
  // and the answers are as good. "default" and "none" ask for no thinking, which
  // a model that cannot do without takes as its own default.
  reasoningEffort: z.enum(["default", "none", "minimal", "low", "medium", "high", "xhigh", "max"]).default("low"),
  // A server Pi has no provider for (Ollama, LM Studio, vLLM, a proxy): where it is and which API it speaks.
  endpoint: z
    .object({
      baseUrl: z.url("must be an address such as http://localhost:11434/v1"),
      api: z.enum(["openai-completions", "openai-responses", "anthropic-messages"]).default("openai-completions"),
      // The environment variable that holds its key; empty for a server that asks for none.
      apiKeyEnv: z.string().default(""),
      images: z.boolean().default(false),
      reasoning: z.boolean().default(false),
      contextWindow: z.number().int().min(1024).default(32_768),
      maxTokens: z.number().int().min(256).default(4096),
      // Pi's compatibility settings for the API, for a server that needs one changed.
      compat: z.record(z.string(), z.unknown()).default({}),
    })
    .nullable()
    .default(null),
  stt: z
    .object({
      model: z.string().min(1).default("small.en"),
      device: z.enum(["auto", "cuda", "cpu"]).default("auto"),
      python: z.string().default(""),
    })
    .prefault({}),
  proactive: z
    .object({
      greet: z.boolean().default(true),
      morningBriefing: z.boolean().default(true),
      reminders: z.boolean().default(true),
      tend: z.boolean().default(true),
      tendMinutes: z.number().min(5).max(24 * 60).default(60),
      quietHours: z.tuple([clockTime, clockTime]).nullable().default(["22:30", "06:30"]),
    })
    .prefault({}),
  keepUtterances: z.number().int().min(0).max(1000).default(20),
  stateDir: z.string().min(1).default("~/.local/state/mirror-companion"),
});

/**
 * @typedef {z.infer<typeof schema> & { path: string, onDisk?: boolean }} Config
 *   `onDisk` is set for a config that was read from the file at `path`, so that a setting changed while
 *   running may be written back there.
 */

export function defaultConfigPath(env = process.env) {
  return env.MIRROR_COMPANION_CONFIG || path.join(os.homedir(), ".config", "mirror-companion", "config.json");
}

/** Where the "login" command keeps its sign-ins: beside the config, readable by its owner only. */
export function defaultAuthPath(env = process.env) {
  return env.MIRROR_COMPANION_AUTH || path.join(path.dirname(defaultConfigPath(env)), "auth.json");
}

/** "~/x" becomes the full path below the home directory. */
export function expandHome(value) {
  if (value === "~") return os.homedir();
  if (value.startsWith("~/") || value.startsWith("~\\")) return path.join(os.homedir(), value.slice(2));
  return value;
}

/** Where the install script puts the Python that runs speech-to-text. */
export function defaultPython(stateDir) {
  return process.platform === "win32"
    ? path.join(stateDir, "venv", "Scripts", "python.exe")
    : path.join(stateDir, "venv", "bin", "python");
}

/**
 * Checks a config as it is written in the file and fills in what it leaves out.
 *
 * @param {unknown} raw
 * @param {string} file Used in error messages only.
 * @returns {Config}
 */
export function parseConfig(raw, file = "config.json") {
  const result = schema.safeParse(raw);
  if (!result.success) {
    const problems = result.error.issues.map((issue) => `${issue.path.join(".") || "the file"}: ${issue.message}`);
    throw new Error(`${file} is not usable. ${problems.join("; ")}. Correct it and start again.`);
  }
  const config = result.data;
  const stateDir = path.resolve(expandHome(config.stateDir));
  return {
    ...config,
    stateDir,
    stt: { ...config.stt, python: config.stt.python ? expandHome(config.stt.python) : defaultPython(stateDir) },
    path: file,
  };
}

/** @returns {Config} */
export function loadConfig(file = defaultConfigPath()) {
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") {
      throw new Error(`There is no config at ${file}. Create one with: node src/cli.js init`);
    }
    throw new Error(`The config at ${file} could not be read: ${error.message}`);
  }
  let raw;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    throw new Error(`The config at ${file} is not valid JSON: ${error.message}`);
  }
  return { ...parseConfig(raw, file), path: path.resolve(file), onDisk: true };
}

/** The config as "init" writes it: every setting spelled out, a fresh secret, no mirror yet. */
export function freshConfig() {
  return {
    listen: { host: "0.0.0.0", port: 8790 },
    secret: crypto.randomBytes(24).toString("base64url"),
    mirror: { host: "", port: 8787, token: "" },
    provider: "copilot-cli",
    model: "gpt-6-luna",
    reasoningEffort: "low",
    stt: { model: "small.en", device: "auto", python: "" },
    proactive: { greet: true, morningBriefing: true, reminders: true, tend: true, tendMinutes: 60, quietHours: ["22:30", "06:30"] },
    keepUtterances: 20,
    stateDir: "~/.local/state/mirror-companion",
  };
}

/**
 * Writes the config so that only its owner can read it: it holds the secret
 * and the mirror's token.
 */
export function writeConfig(file, raw, { overwrite = false } = {}) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  fs.writeFileSync(file, JSON.stringify(raw, null, 2) + "\n", { mode: 0o600, flag: overwrite ? "w" : "wx" });
  fs.chmodSync(file, 0o600);
}

/** Changes some settings in the file and leaves the rest as the owner wrote them. */
export function updateConfigFile(file, change) {
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  change(raw);
  parseConfig(raw, file);
  writeConfig(file, raw, { overwrite: true });
}

/** True when other accounts on the machine could read the file. Always false on Windows. */
export function readableByOthers(file) {
  if (process.platform === "win32") return false;
  try {
    return (fs.statSync(file).mode & 0o077) !== 0;
  } catch {
    return false;
  }
}
