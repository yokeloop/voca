import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import type { VocaConfig } from './types.js';
import { binDir, configPath } from './paths.js';

export { configPath };

export const defaultConfig: VocaConfig = {
  profile: 'personal',
  wakeWord: 'hey_jarvis',
  stopWord: 'stop',
  piperModel: 'bin/ru_RU-irina-medium.onnx',
  piperBin: 'bin/piper',
  language: 'ru',
};

export async function ensureConfigDir(file: string = configPath()): Promise<void> {
  await fs.mkdir(path.dirname(file), { recursive: true });
}

export async function readConfig(file: string = configPath()): Promise<VocaConfig> {
  try {
    const raw = await fs.readFile(file, 'utf-8');
    const parsed = JSON.parse(raw) as Partial<VocaConfig>;
    return { ...defaultConfig, ...parsed };
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return { ...defaultConfig };
    throw err;
  }
}

export function resolvePiperBin(cfg: VocaConfig): string {
  const value = cfg.piperBin;
  if (path.isAbsolute(value)) return value;
  return path.join(binDir(), value.replace(/^bin\//, ''));
}

export function resolvePiperModel(cfg: VocaConfig): string {
  const model = cfg.piperModel;
  if (path.isAbsolute(model)) return model.endsWith('.onnx') ? model : `${model}.onnx`;
  const name = model.replace(/^bin\//, '').replace(/\.onnx$/, '');
  return path.join(binDir(), `${name}.onnx`);
}

export async function getAvailableProfiles(): Promise<string[]> {
  const openclawConfigPath = path.join(os.homedir(), '.openclaw/openclaw.json');
  try {
    const raw = await fs.readFile(openclawConfigPath, 'utf-8');
    const data = JSON.parse(raw);
    const list = data?.agents?.list;
    if (Array.isArray(list) && list.length > 0) {
      return list.map((a: any) => a.id).filter(Boolean);
    }
  } catch {
    // openclaw config not found or invalid
  }
  return ['personal', 'public']; // fallback
}

export async function writeConfig(cfg: VocaConfig, file: string = configPath()): Promise<void> {
  await ensureConfigDir(file);
  await fs.writeFile(file, JSON.stringify(cfg, null, 2) + '\n', 'utf-8');
}
