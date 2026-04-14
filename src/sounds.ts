import { execFile } from 'node:child_process';
import path from 'node:path';
import { soundsDir } from './paths.js';

export type SoundType = 'wake' | 'stop' | 'error';

export function soundFile(type: SoundType): string {
  return path.join(soundsDir(), `${type}.wav`);
}

export function playSound(
  type: SoundType,
  opts: { device?: string },
): Promise<void> {
  return new Promise((resolve, reject) => {
    const args = opts.device !== undefined
      ? ['-D', opts.device, soundFile(type)]
      : [soundFile(type)];
    execFile('aplay', args, (error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}
