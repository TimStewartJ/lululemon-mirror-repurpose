import fs from "node:fs";
import path from "node:path";

const MAX_LINES = 60;
const MAX_LENGTH = 200;

/**
 * What the companion remembers about the household: one fact per line in a
 * text file its owner can read and edit.
 *
 * @param {string} stateDir
 */
export function createMemory(stateDir) {
  const file = path.join(stateDir, "memory.txt");

  function lines() {
    try {
      return fs
        .readFileSync(file, "utf8")
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
    } catch (error) {
      if (error.code === "ENOENT") return [];
      throw error;
    }
  }

  function save(all) {
    fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
    fs.writeFileSync(file, all.length > 0 ? all.join("\n") + "\n" : "", { mode: 0o600 });
  }

  return {
    file,
    lines,

    /**
     * Adds one line.
     *
     * @returns {{ remembered: string } | { error: string }}
     */
    remember(note) {
      const line = String(note ?? "").replace(/\s+/g, " ").trim();
      if (!line) return { error: "There is nothing to remember: the note is empty." };
      if (line.length > MAX_LENGTH) {
        return { error: `A note can be up to ${MAX_LENGTH} characters. Say it more briefly.` };
      }
      const all = lines();
      if (all.some((existing) => existing.toLowerCase() === line.toLowerCase())) return { remembered: line };
      if (all.length >= MAX_LINES) {
        return { error: `The memory is full at ${MAX_LINES} notes. Forget one that no longer matters first.` };
      }
      save([...all, line]);
      return { remembered: line };
    },

    /**
     * Removes every line that contains the text, whatever its case.
     *
     * @returns {{ forgotten: string[] } | { error: string }}
     */
    forget(containing) {
      const needle = String(containing ?? "").trim().toLowerCase();
      if (needle.length < 3) return { error: "Give at least three characters of the note to forget." };
      const all = lines();
      const kept = all.filter((line) => !line.toLowerCase().includes(needle));
      if (kept.length === all.length) {
        return { error: `No note contains "${containing}". The notes are listed in your instructions.` };
      }
      save(kept);
      return { forgotten: all.filter((line) => !kept.includes(line)) };
    },
  };
}
