import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import type { VocaConfig } from './types.js';

export const CONFIG_PATH = path.join(os.homedir(), '.openclaw/assistant/config.json');

export const defaultConfig: VocaConfig = {
  inputDevice: 'plughw:2,0',
  outputDevice: 'plughw:2,0',
  profile: 'personal',
  wakeWord: 'hey_jarvis',
  stopWord: 'stop',
  piperModel: 'ru_RU-irina-medium',
  piperBin: path.join(os.homedir(), '.openclaw/assistant/bin/piper'),
  language: 'ru',
};

export async function ensureConfigDir(configPath: string = CONFIG_PATH): Promise<void> {
  await fs.mkdir(path.dirname(configPath), { recursive: true });
}

export async function readConfig(configPath: string = CONFIG_PATH): Promise<VocaConfig> {
  try {
    const raw = await fs.readFile(configPath, 'utf-8');
    const parsed = JSON.parse(raw) as Partial<VocaConfig>;
    return { ...defaultConfig, ...parsed };
  } catch (err: unknown) {
    if (err instanceof Error && 'code' in err && (err as NodeJS.ErrnoException).code === 'ENOENT') {
      return { ...defaultConfig };
    }
    throw err;
  }
}

export async function writeConfig(cfg: VocaConfig, configPath: string = CONFIG_PATH): Promise<void> {
  await ensureConfigDir(configPath);
  await fs.writeFile(configPath, JSON.stringify(cfg, null, 2) + '\n', 'utf-8');
}
