import fs from "node:fs";
import path from "node:path";

/**
 * Keeps the last few recordings of what was said to the mirror, so that a
 * mishearing can be listened to afterwards. Older ones are deleted.
 *
 * @param {string} stateDir
 * @param {number} keep How many to keep; 0 keeps none and removes what is there.
 * @param {(event: string, fields?: object) => void} [log]
 */
export function createRecordings(stateDir, keep, log = () => {}) {
  const folder = path.join(stateDir, "utterances");

  function prune() {
    let names;
    try {
      names = fs.readdirSync(folder).filter((name) => name.endsWith(".wav")).sort();
    } catch (error) {
      if (error.code === "ENOENT") return;
      throw error;
    }
    // Names start with the time they were written, so sorting them sorts by age.
    for (const name of names.slice(0, Math.max(0, names.length - keep))) {
      fs.rmSync(path.join(folder, name), { force: true });
    }
  }

  return {
    folder,

    /**
     * Stores one recording. A failure is logged and otherwise ignored: the
     * request it belongs to matters more than its recording.
     *
     * @param {string} id The utterance id; only letters, digits and dashes.
     * @param {Buffer} wav
     * @param {number} at
     */
    save(id, wav, at) {
      try {
        if (keep > 0) {
          fs.mkdirSync(folder, { recursive: true, mode: 0o700 });
          const stamp = new Date(at).toISOString().replace(/[-:.]/g, "");
          fs.writeFileSync(path.join(folder, `${stamp}-${id}.wav`), wav, { mode: 0o600 });
        }
        prune();
      } catch (error) {
        log("recording.unsaved", { detail: error.message });
      }
    },
  };
}
