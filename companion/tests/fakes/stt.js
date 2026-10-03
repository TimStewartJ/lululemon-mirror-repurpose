import { writeWav } from "../../src/wav.js";

/**
 * A recording that "says" the given words to the fake speech-to-text: the
 * words are written into the samples themselves, so the WAV's bytes decide
 * the transcript.
 *
 * @param {string} words
 * @param {number} [seconds]
 * @returns {Buffer} a 16 kHz mono 16-bit WAV file
 */
export function speech(words, seconds = 1) {
  const samples = new Int16Array(Math.max(16000 * seconds, words.length + 1)).fill(1);
  samples[0] = words.length;
  for (let index = 0; index < words.length; index++) samples[index + 1] = words.charCodeAt(index);
  return writeWav(samples);
}

/**
 * Speech-to-text for the tests: reads back what {@link speech} wrote.
 *
 * @param {import("../../src/clock.js").Clock} clock
 */
export function fakeStt(clock) {
  const stt = {
    ready: true,
    device: "cuda",
    /** Set to an error to make the next transcription fail. */
    fail: null,
    /** How long a transcription takes on the fake clock. */
    takesMs: 0,
    heard: 0,

    start() {},
    async stop() {},
    health: () => ({ ready: stt.ready, model: "small.en", device: stt.device, detail: "" }),

    async transcribe(pcm) {
      stt.heard += 1;
      if (stt.takesMs > 0) await new Promise((resolve) => clock.setTimeout(resolve, stt.takesMs));
      if (stt.fail) {
        const error = stt.fail;
        stt.fail = null;
        throw error;
      }
      const length = pcm.readInt16LE(0);
      let text = "";
      for (let index = 1; index <= length; index++) text += String.fromCharCode(pcm.readUInt16LE(index * 2));
      return { text, ms: stt.takesMs };
    },
  };
  return stt;
}
