import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import type { VocaConfig } from './types.js';

export const CONFIG_PATH = path.join(os.homedir(), '.openclaw/assistant/config.json');

export const defaultConfig: VocaConfig = {
  profile: 'personal',
  wakeWord: 'hey_jarvis',
  stopWord: 'stop',
  piperModel: path.join(os.homedir(), '.openclaw/assistant/bin/ru_RU-irina-medium.onnx'),
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
    const cfg = { ...defaultConfig, ...parsed };
    cfg.piperModel = resolvePiperModel(cfg.piperModel);
    return cfg;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return { ...defaultConfig };
    throw err;
  }
}

function resolvePiperModel(model: string): string {
  if (path.isAbsolute(model) && model.endsWith('.onnx')) return model;
  const name = model.replace(/\.onnx$/, '');
  return path.join(os.homedir(), '.openclaw/assistant/bin', `${name}.onnx`);
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

export async function writeConfig(cfg: VocaConfig, configPath: string = CONFIG_PATH): Promise<void> {
  await ensureConfigDir(configPath);
  await fs.writeFile(configPath, JSON.stringify(cfg, null, 2) + '\n', 'utf-8');
}
