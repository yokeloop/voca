import { describe, it, expect, vi, beforeEach } from 'vitest';

const { execFileMock } = vi.hoisted(() => ({
  execFileMock: vi.fn((_cmd: string, _args: string[], cb: (err: Error | null) => void) => {
    cb(null);
  }),
}));

vi.mock('node:child_process', () => ({
  execFile: execFileMock,
}));

import { playSound } from '../src/sounds.js';

describe('playSound', () => {
  beforeEach(() => {
    execFileMock.mockClear();
  });

  it('passes -D when device is set', async () => {
    await playSound('wake', { device: 'hw:0,0' });
    const [cmd, args] = execFileMock.mock.calls[0];
    expect(cmd).toBe('aplay');
    expect(args).toContain('-D');
    expect(args).toContain('hw:0,0');
  });

  it('omits -D when device is undefined', async () => {
    await playSound('wake', { device: undefined });
    const [cmd, args] = execFileMock.mock.calls[0];
    expect(cmd).toBe('aplay');
    expect(args).not.toContain('-D');
  });
});
