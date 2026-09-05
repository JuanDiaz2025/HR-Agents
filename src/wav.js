/**
 * Just enough WAV handling to split Twilio's dual-channel recording into two
 * mono tracks. Twilio puts the caller (our AI seller) on channel 1 and the
 * answering party (the rep) on channel 2, so splitting first gives us real
 * speaker labels without paying for diarization.
 */

function findChunk(buf, id, from = 12) {
  let offset = from;
  while (offset + 8 <= buf.length) {
    const chunkId = buf.toString('ascii', offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    if (chunkId === id) return { offset: offset + 8, size };
    offset += 8 + size + (size % 2);
  }
  return null;
}

export function parseWav(buf) {
  if (buf.toString('ascii', 0, 4) !== 'RIFF' || buf.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error('Not a RIFF/WAVE file');
  }
  const fmt = findChunk(buf, 'fmt ');
  const data = findChunk(buf, 'data');
  if (!fmt || !data) throw new Error('WAV is missing a fmt or data chunk');
  return {
    audioFormat: buf.readUInt16LE(fmt.offset),
    channels: buf.readUInt16LE(fmt.offset + 2),
    sampleRate: buf.readUInt32LE(fmt.offset + 4),
    bitsPerSample: buf.readUInt16LE(fmt.offset + 14),
    data: buf.subarray(data.offset, Math.min(data.offset + data.size, buf.length)),
  };
}

export function buildWav({ channels, sampleRate, bitsPerSample, data }) {
  const header = Buffer.alloc(44);
  const byteRate = sampleRate * channels * (bitsPerSample / 8);
  header.write('RIFF', 0, 'ascii');
  header.writeUInt32LE(36 + data.length, 4);
  header.write('WAVE', 8, 'ascii');
  header.write('fmt ', 12, 'ascii');
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(channels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(byteRate, 28);
  header.writeUInt16LE(channels * (bitsPerSample / 8), 32);
  header.writeUInt16LE(bitsPerSample, 34);
  header.write('data', 36, 'ascii');
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

/** Returns { left, right } as complete mono WAV buffers, or null if the input is already mono. */
export function splitStereoWav(buf) {
  const wav = parseWav(buf);
  if (wav.channels !== 2) return null;
  if (wav.bitsPerSample !== 16) throw new Error(`Expected 16-bit PCM, got ${wav.bitsPerSample}-bit`);

  const bytesPerSample = 2;
  const frames = Math.floor(wav.data.length / (bytesPerSample * 2));
  const left = Buffer.alloc(frames * bytesPerSample);
  const right = Buffer.alloc(frames * bytesPerSample);

  for (let i = 0; i < frames; i++) {
    left.writeInt16LE(wav.data.readInt16LE(i * 4), i * 2);
    right.writeInt16LE(wav.data.readInt16LE(i * 4 + 2), i * 2);
  }

  const meta = { channels: 1, sampleRate: wav.sampleRate, bitsPerSample: 16 };
  return { left: buildWav({ ...meta, data: left }), right: buildWav({ ...meta, data: right }) };
}
