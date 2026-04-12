import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { readConfig, writeConfig } from './config.js';
import { run, fileExists } from './util.js';

const CATALOG_URL =
  'https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/voices.json';
const PIPER_DIR = path.join(os.homedir(), '.openclaw/assistant/bin');

export interface CatalogEntry {
  language: { code: string };
  quality: string;
}

export type Catalog = Record<string, CatalogEntry>;

let catalogCache: Catalog | null = null;

export function __resetCatalogCacheForTests(): void {
  catalogCache = null;
}

export async function fetchCatalog(): Promise<Catalog> {
  if (catalogCache) return catalogCache;
  const res = await fetch(CATALOG_URL);
  if (!res.ok) {
    throw new Error(`catalog fetch failed: HTTP ${res.status}`);
  }
  const parsed = (await res.json()) as Catalog;
  catalogCache = parsed;
  return parsed;
}

export interface VoicePaths {
  onnxUrl: string;
  jsonUrl: string;
  onnxPath: string;
  jsonPath: string;
}

export function deriveVoicePaths(name: string): VoicePaths {
  const parts = name.split('-');
  if (parts.length < 3) {
    throw new Error(`invalid voice name: ${name} (expected <lang_code>-<name>-<quality>)`);
  }
  const [langCode, voiceName, quality] = [parts[0], parts.slice(1, -1).join('-'), parts[parts.length - 1]];
  const lang = langCode.split('_')[0].toLowerCase();
  const base = `https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/${lang}/${langCode}/${voiceName}/${quality}`;
  return {
    onnxUrl: `${base}/${name}.onnx`,
    jsonUrl: `${base}/${name}.onnx.json`,
    onnxPath: path.join(PIPER_DIR, `${name}.onnx`),
    jsonPath: path.join(PIPER_DIR, `${name}.onnx.json`),
  };
}

export async function listInstalled(): Promise<string[]> {
  let entries: string[];
  try {
    entries = await fs.readdir(PIPER_DIR);
  } catch {
    return [];
  }
  const installed: string[] = [];
  for (const entry of entries) {
    if (!entry.endsWith('.onnx')) continue;
    const name = entry.replace(/\.onnx$/, '');
    if (await fileExists(path.join(PIPER_DIR, `${name}.onnx.json`))) {
      installed.push(name);
    }
  }
  return installed.sort();
}

export interface ListAvailableOptions {
  languageFilter?: string;
  all?: boolean;
}

export async function listAvailable(
  opts: ListAvailableOptions = {},
): Promise<Array<{ name: string; langCode: string; quality: string }>> {
  const catalog = await fetchCatalog();
  const results: Array<{ name: string; langCode: string; quality: string }> = [];
  for (const [name, entry] of Object.entries(catalog)) {
    const langCode = entry.language?.code ?? '';
    if (!opts.all && opts.languageFilter && !name.startsWith(`${opts.languageFilter}_`)) {
      continue;
    }
    results.push({ name, langCode, quality: entry.quality });
  }
  results.sort((a, b) => a.name.localeCompare(b.name));
  return results;
}

export async function installVoice(name: string): Promise<void> {
  const catalog = await fetchCatalog();
  if (!(name in catalog)) {
    throw new Error(`Unknown voice: ${name}. Run: voca voice available`);
  }
  const paths = deriveVoicePaths(name);

  if ((await fileExists(paths.onnxPath)) && (await fileExists(paths.jsonPath))) {
    console.log(`Voice already installed: ${name}`);
    return;
  }

  await fs.mkdir(PIPER_DIR, { recursive: true });

  try {
    console.log(`Downloading ${name}.onnx...`);
    await run('curl', ['--fail', '-L', '-o', paths.onnxPath, paths.onnxUrl]);
    console.log(`Downloading ${name}.onnx.json...`);
    await run('curl', ['--fail', '-L', '-o', paths.jsonPath, paths.jsonUrl]);
    console.log(`Voice installed: ${name}`);
  } catch (err) {
    await fs.unlink(paths.onnxPath).catch(() => {});
    await fs.unlink(paths.jsonPath).catch(() => {});
    throw err;
  }
}

export async function useVoice(name: string): Promise<void> {
  const catalog = await fetchCatalog();
  if (!(name in catalog)) {
    throw new Error(`Unknown voice: ${name}. Run: voca voice available`);
  }
  const paths = deriveVoicePaths(name);

  if (!(await fileExists(paths.onnxPath))) {
    await installVoice(name);
  }

  const config = await readConfig();
  if (config.piperModel === paths.onnxPath) {
    console.log(`Already using voice: ${name}`);
    return;
  }

  config.piperModel = paths.onnxPath;
  await writeConfig(config);
  console.log(`Switched to voice: ${name}. Restart the daemon (voca stop && voca start) to apply.`);
}
