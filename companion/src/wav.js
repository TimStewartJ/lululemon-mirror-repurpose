/**
 * Reads the WAV files the mirror sends: 16 kHz, mono, 16-bit PCM. Only the
 * format is checked strictly. The sizes a recorder writes into the header are
 * often wrong when it wrote the header before it knew the length, so the
 * sound is taken to run to the end of the file.
 *
 * @param {Buffer} file
 * @returns {{ pcm: Buffer, seconds: number, silent: boolean } | { problem: string }}
 */
export function readWav(file) {
  if (file.length < 44 || file.toString("latin1", 0, 4) !== "RIFF" || file.toString("latin1", 8, 12) !== "WAVE") {
    return { problem: "The body is not a WAV file." };
  }
  let offset = 12;
  let format = null;
  while (offset + 8 <= file.length) {
    const id = file.toString("latin1", offset, offset + 4);
    const size = file.readUInt32LE(offset + 4);
    const start = offset + 8;
    if (id === "fmt ") {
      if (start + 16 > file.length) break;
      format = {
        encoding: file.readUInt16LE(start),
        channels: file.readUInt16LE(start + 2),
        rate: file.readUInt32LE(start + 4),
        bits: file.readUInt16LE(start + 14),
      };
    } else if (id === "data") {
      if (!format) break;
      if (format.encoding !== 1 || format.channels !== 1 || format.rate !== 16000 || format.bits !== 16) {
        return {
          problem:
            "The WAV file must be 16 kHz, mono, 16-bit PCM. This one is " +
            `${format.rate} Hz, ${format.channels} channel(s), ${format.bits}-bit, format ${format.encoding}.`,
        };
      }
      const declared = start + size;
      const end = size > 0 && declared <= file.length ? declared : file.length;
      const length = (end - start) & ~1;
      const pcm = file.subarray(start, start + length);
      return { pcm, seconds: length / 2 / 16000, silent: isSilent(pcm) };
    }
    // Chunks are padded to an even length.
    offset = start + size + (size % 2);
  }
  return { problem: "The WAV file has no sound in it: its format or data part is missing." };
}

/** True when every sample is zero, which is what a microphone that delivers nothing looks like. */
function isSilent(pcm) {
  for (let index = 0; index < pcm.length; index++) {
    if (pcm[index] !== 0) return false;
  }
  return true;
}

/**
 * Builds a WAV file from samples; used by tests and scripts.
 *
 * @param {Int16Array} samples 16 kHz mono
 */
export function writeWav(samples) {
  const data = Buffer.from(samples.buffer, samples.byteOffset, samples.byteLength);
  const header = Buffer.alloc(44);
  header.write("RIFF", 0, "latin1");
  header.writeUInt32LE(36 + data.length, 4);
  header.write("WAVEfmt ", 8, "latin1");
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(16000, 24);
  header.writeUInt32LE(32000, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36, "latin1");
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}
