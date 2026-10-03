import assert from "node:assert/strict";
import test from "node:test";
import { readWav, writeWav } from "../src/wav.js";

const tone = () => new Int16Array(16000).map((_, index) => Math.round(8000 * Math.sin(index / 10)));

test("a 16 kHz mono 16-bit WAV gives its samples and length", () => {
  const sound = readWav(writeWav(tone()));
  assert.equal(sound.problem, undefined);
  assert.equal(sound.seconds, 1);
  assert.equal(sound.silent, false);
  assert.equal(sound.pcm.length, 32000);
});

test("silence is recognised", () => {
  assert.equal(readWav(writeWav(new Int16Array(8000))).silent, true);
});

test("a header with a wrong length is tolerated", () => {
  // A recorder that streams writes the header before it knows the length.
  const wav = writeWav(tone());
  wav.writeUInt32LE(0xffffffff, 4);
  wav.writeUInt32LE(0xffffffff, 40);
  assert.equal(readWav(wav).seconds, 1);
  wav.writeUInt32LE(0, 40);
  assert.equal(readWav(wav).seconds, 1);
});

test("extra parts before the sound are skipped", () => {
  const wav = writeWav(tone());
  const list = Buffer.alloc(8 + 5 + 1);
  list.write("LIST", 0, "latin1");
  list.writeUInt32LE(5, 4);
  const withList = Buffer.concat([wav.subarray(0, 36), list, wav.subarray(36)]);
  assert.equal(readWav(withList).seconds, 1);
});

test("what is not such a WAV is refused with the reason", () => {
  assert.match(readWav(Buffer.from("not a wav file at all, just some text that is long enough")).problem, /not a WAV file/);
  assert.match(readWav(Buffer.alloc(10)).problem, /not a WAV file/);
  const stereo = writeWav(tone());
  stereo.writeUInt16LE(2, 22);
  assert.match(readWav(stereo).problem, /16 kHz, mono, 16-bit PCM.*2 channel/);
  const fast = writeWav(tone());
  fast.writeUInt32LE(44100, 24);
  assert.match(readWav(fast).problem, /44100 Hz/);
  assert.match(readWav(writeWav(tone()).subarray(0, 36).fill(0, 12)).problem, /not a WAV file|no sound/);
});
