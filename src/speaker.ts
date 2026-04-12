import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';

export class SpeakerError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SpeakerError';
  }
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
  device: string;
}): Promise<void> {
  const sampleRate = await readSampleRate(opts.piperModel);

  return new Promise<void>((resolve, reject) => {
    const piper = spawn(opts.piperBin, [
      '--model', opts.piperModel,
      '--output_raw',
    ], {
      stdio: ['pipe', 'pipe', 'inherit'],
    });

    const aplay = spawn('aplay', [
      '-D', opts.device,
      '-r', String(sampleRate),
      '-f', 'S16_LE',
      '-c', '1',
    ], {
      stdio: ['pipe', 'inherit', 'inherit'],
    });

    piper.stdout.pipe(aplay.stdin);

    let piperExitCode: number | null = null;
    let settled = false;

    piper.on('error', (err) => {
      if (!settled) {
        settled = true;
        reject(new SpeakerError(`piper spawn error: ${err.message}`));
      }
    });

    aplay.on('error', (err) => {
      if (!settled) {
        settled = true;
        reject(new SpeakerError(`aplay spawn error: ${err.message}`));
      }
    });

    piper.on('exit', (code) => {
      piperExitCode = code;
    });

    aplay.on('close', async (code) => {
      if (settled) return;
      settled = true;

      if (piperExitCode !== null && piperExitCode !== 0) {
        reject(new SpeakerError(`piper exited with code ${piperExitCode}`));
        return;
      }

      if (code !== 0 && code !== 1) {
        reject(new SpeakerError(`aplay exited with code ${code}`));
        return;
      }

      await sleep(500);
      resolve();
    });

    piper.stdin.write(opts.text + '\n');
    piper.stdin.end();
  });
}
