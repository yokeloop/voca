import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import { sanitizeForTts } from './sanitizer.js';

export class SpeakerError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SpeakerError';
  }
}

export interface SpeechHandle {
  done: Promise<void>;
  interrupt(): void;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readSampleRate(modelPath: string): Promise<number> {
  const jsonPath = `${modelPath}.json`;
  try {
    const raw = await fs.readFile(jsonPath, 'utf-8');
    const parsed = JSON.parse(raw);
    const rate = parsed?.audio?.sample_rate;
    if (typeof rate !== 'number') {
      throw new Error('missing audio.sample_rate');
    }
    return rate;
  } catch (err) {
    throw new SpeakerError(
      `voice metadata unreadable for ${modelPath}: ${(err as Error).message}`,
    );
  }
}

export async function speak(opts: {
  text: string;
  piperBin: string;
  piperModel: string;
  device?: string;
}): Promise<SpeechHandle> {
  const sampleRate = await readSampleRate(opts.piperModel);

  let settled = false;
  let resolveDone!: () => void;
  let rejectDone!: (err: Error) => void;

  const done = new Promise<void>((resolve, reject) => {
    resolveDone = resolve;
    rejectDone = reject;
  });

  const piper = spawn(opts.piperBin, [
    '--model', opts.piperModel,
    '--output_raw',
  ], {
    stdio: ['pipe', 'pipe', 'inherit'],
  });

  const aplayArgs = [
    ...(opts.device !== undefined ? ['-D', opts.device] : []),
    '-r', String(sampleRate),
    '-f', 'S16_LE',
    '-c', '1',
  ];
  const aplay = spawn('aplay', aplayArgs, {
    stdio: ['pipe', 'inherit', 'inherit'],
  });

  piper.stdout.pipe(aplay.stdin);

  let piperExitCode: number | null = null;

  piper.on('error', (err) => {
    if (!settled) {
      settled = true;
      rejectDone(new SpeakerError(`piper spawn error: ${err.message}`));
    }
  });

  aplay.on('error', (err) => {
    if (!settled) {
      settled = true;
      rejectDone(new SpeakerError(`aplay spawn error: ${err.message}`));
    }
  });

  piper.on('exit', (code) => {
    piperExitCode = code;
  });

  aplay.on('close', async (code) => {
    if (settled) return;
    settled = true;

    if (piperExitCode !== null && piperExitCode !== 0) {
      rejectDone(new SpeakerError(`piper exited with code ${piperExitCode}`));
      return;
    }

    if (code !== 0 && code !== 1) {
      rejectDone(new SpeakerError(`aplay exited with code ${code}`));
      return;
    }

    await sleep(500);
    resolveDone();
  });

  piper.stdin.write(sanitizeForTts(opts.text) + '\n');
  piper.stdin.end();

  const interrupt = (): void => {
    if (settled) return;
    settled = true;
    try { piper.kill('SIGTERM'); } catch { /* best-effort */ }
    try { aplay.kill('SIGTERM'); } catch { /* best-effort */ }
    resolveDone();
  };

  return { done, interrupt };
}
