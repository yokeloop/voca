import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('node:child_process', () => ({
  execFile: vi.fn(),
}));

import { execFile } from 'node:child_process';
import { transcribe, TranscribeError } from '../src/transcriber.js';

function mockExecFile(stdout: string) {
  (execFile as unknown as ReturnType<typeof vi.fn>).mockImplementation(
    (_cmd: string, _args: string[], _opts: object, cb: Function) => {
      cb(null, { stdout, stderr: '' });
    },
  );
}

function mockExecFileError(stderr: string) {
  (execFile as unknown as ReturnType<typeof vi.fn>).mockImplementation(
    (_cmd: string, _args: string[], _opts: object, cb: Function) => {
      const err = Object.assign(new Error('command failed'), { stderr });
      cb(err);
    },
  );
}

describe('transcriber', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns normal text as-is', async () => {
    mockExecFile('Привет мир\n');
    const result = await transcribe('/tmp/test.wav');
    expect(result).toBe('Привет мир');
  });

  it('trims trailing "stop " (lowercase)', async () => {
    mockExecFile('Открой дверь stop \n');
    const result = await transcribe('/tmp/test.wav');
    expect(result).toBe('Открой дверь');
  });

  it('trims trailing "STOP" (uppercase)', async () => {
    mockExecFile('Включи свет STOP');
    const result = await transcribe('/tmp/test.wav');
    expect(result).toBe('Включи свет');
  });

  it('returns empty string when text is only "stop"', async () => {
    mockExecFile('stop\n');
    const result = await transcribe('/tmp/test.wav');
    expect(result).toBe('');
  });

  it('throws TranscribeError on execFile error', async () => {
    mockExecFileError('whisper failed');
    await expect(transcribe('/tmp/test.wav')).rejects.toThrow(TranscribeError);
    await expect(transcribe('/tmp/test.wav')).rejects.toThrow('whisper failed');
  });

  it('passes language option to whisper-stt-wrapper', async () => {
    mockExecFile('hello world\n');
    await transcribe('/tmp/test.wav', { language: 'en' });
    expect(execFile).toHaveBeenCalledWith(
      '/usr/local/bin/whisper-stt-wrapper',
      ['/tmp/test.wav', '--language', 'en'],
      { maxBuffer: 1024 * 1024 },
      expect.any(Function),
    );
  });

  it('defaults language to en', async () => {
    mockExecFile('text\n');
    await transcribe('/tmp/test.wav');
    expect(execFile).toHaveBeenCalledWith(
      '/usr/local/bin/whisper-stt-wrapper',
      ['/tmp/test.wav', '--language', 'en'],
      { maxBuffer: 1024 * 1024 },
      expect.any(Function),
    );
  });
});
