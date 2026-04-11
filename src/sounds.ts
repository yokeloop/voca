import { execFile } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';

export const SOUNDS_DIR = path.join(
  os.homedir(),
  '.openclaw',
  'assistant',
  'sounds',
);

export type SoundType = 'wake' | 'stop' | 'error';

export function soundFile(type: SoundType): string {
  return path.join(SOUNDS_DIR, `${type}.wav`);
}

export function playSound(
  type: SoundType,
  opts: { device: string },
): Promise<void> {
  return new Promise((resolve, reject) => {
    execFile('aplay', ['-D', opts.device, soundFile(type)], (error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}
