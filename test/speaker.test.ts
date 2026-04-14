import { describe, it, expect, vi, beforeEach } from 'vitest';
import { EventEmitter } from 'node:events';

function makeChild() {
  const stdout = new EventEmitter() as any;
  stdout.pipe = vi.fn();
  const stdin = {
    write: vi.fn(),
    end: vi.fn(),
  };
  const child = new EventEmitter() as any;
  child.stdout = stdout;
  child.stdin = stdin;
  return child;
}

const { spawnMock, readFileMock } = vi.hoisted(() => ({
  spawnMock: vi.fn(),
  readFileMock: vi.fn(async () => JSON.stringify({ audio: { sample_rate: 22050 } })),
}));

vi.mock('node:child_process', () => ({
  spawn: spawnMock,
}));

vi.mock('node:fs/promises', () => ({
  default: { readFile: readFileMock },
  readFile: readFileMock,
}));

import { speak } from '../src/speaker.js';

describe('speak', () => {
  beforeEach(() => {
    spawnMock.mockReset();
  });

  async function runSpeak(device: string | undefined) {
    const piper = makeChild();
    const aplay = makeChild();
    spawnMock.mockImplementationOnce(() => piper).mockImplementationOnce(() => aplay);

    const handlePromise = speak({
      text: 'hello',
      piperBin: '/usr/bin/piper',
      piperModel: 'model',
      device,
    });

    setTimeout(() => {
      piper.emit('exit', 0);
      aplay.emit('close', 0);
    }, 0);

    const handle = await handlePromise;
    await handle.done;
    return { piper, aplay };
  }

  it('passes -D when device is set', async () => {
    await runSpeak('hw:0,0');
    const aplayCall = spawnMock.mock.calls[1];
    expect(aplayCall[0]).toBe('aplay');
    expect(aplayCall[1]).toContain('-D');
    expect(aplayCall[1]).toContain('hw:0,0');
  });

  it('omits -D when device is undefined', async () => {
    await runSpeak(undefined);
    const aplayCall = spawnMock.mock.calls[1];
    expect(aplayCall[0]).toBe('aplay');
    expect(aplayCall[1]).not.toContain('-D');
  });
});
