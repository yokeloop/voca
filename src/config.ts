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
    const cfg = { ...defaultConfig, ...parsed };
    cfg.piperModel = resolvePiperModel(cfg.piperModel);
    cfg.piperBin = resolveBinPath(cfg.piperBin);
    return cfg;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return { ...defaultConfig };
    throw err;
  }
}

function resolveBinPath(value: string): string {
  if (path.isAbsolute(value)) return value;
  return path.join(binDir(), value.replace(/^bin\//, ''));
}

function resolvePiperModel(model: string): string {
  if (path.isAbsolute(model) && model.endsWith('.onnx')) return model;
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
