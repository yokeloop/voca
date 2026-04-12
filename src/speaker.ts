import { spawn } from 'node:child_process';

export class SpeakerError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SpeakerError';
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function speak(opts: {
  text: string;
  piperBin: string;
  piperModel: string;
  device?: string;
}): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const piper = spawn(opts.piperBin, [
      '--model', opts.piperModel,
      '--output_raw',
    ], {
      stdio: ['pipe', 'pipe', 'inherit'],
    });

    const aplayArgs = [
      ...(opts.device !== undefined ? ['-D', opts.device] : []),
      '-r', '22050',
      '-f', 'S16_LE',
      '-c', '1',
    ];
    const aplay = spawn('aplay', aplayArgs, {
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
