/**
 * One JSON line per event on standard output, which systemd puts in the
 * journal. The secrets given here are cut out of every line, so a mistake
 * elsewhere cannot leak them.
 *
 * @param {Object} [options]
 * @param {(line: string) => void} [options.write]
 * @param {{ now: () => number }} [options.clock]
 * @param {string[]} [options.secrets]
 * @returns {(event: string, fields?: Record<string, unknown>) => void}
 */
export function createLog({ write = writeLine, clock = Date, secrets = [] } = {}) {
  const hidden = secrets.filter((secret) => typeof secret === "string" && secret.length >= 4);
  return function log(event, fields = {}) {
    let line;
    try {
      line = JSON.stringify({ at: new Date(clock.now()).toISOString(), event, ...fields });
    } catch {
      line = JSON.stringify({ at: new Date(clock.now()).toISOString(), event, note: "fields could not be written" });
    }
    for (const secret of hidden) {
      line = line.split(secret).join("[hidden]");
    }
    write(line);
  };
}

function writeLine(line) {
  process.stdout.write(line + "\n");
}

/** What went wrong, as one line of text, whatever was thrown. */
export function describeError(error) {
  if (error instanceof Error) return error.message || error.name;
  return String(error);
}
