import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

function pointerDir(): string {
  return path.join(os.homedir(), '.config', 'voca');
}

function pointerPath(): string {
  return path.join(pointerDir(), 'root');
}

export function readPointerFile(): string | null {
  const file = pointerPath();
  let raw: string;
  try {
    raw = fs.readFileSync(file, 'utf-8');
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw err;
  }
  const trimmed = raw.trim();
  if (!path.isAbsolute(trimmed)) {
    throw new Error(`corrupt pointer file at ${file}: expected absolute path`);
  }
  return trimmed;
}

export async function writePointerFile(absolutePath: string): Promise<void> {
  if (!path.isAbsolute(absolutePath)) {
    throw new Error(`writePointerFile requires an absolute path, got: ${absolutePath}`);
  }
  await fsp.mkdir(pointerDir(), { recursive: true });
  await fsp.writeFile(pointerPath(), absolutePath + '\n', 'utf-8');
}

export function storageRoot(): string {
  const envRoot = process.env.VOCA_HOME;
  if (envRoot && envRoot.length > 0) return envRoot;
  const pointer = readPointerFile();
  if (pointer) return pointer;
  throw new Error("VOCA storage root not configured — run 'voca bootstrap'");
}

export function configPath(): string {
  return path.join(storageRoot(), 'config.json');
}

export function sessionPath(): string {
  return path.join(storageRoot(), 'session.json');
}

export function soundsDir(): string {
  return path.join(storageRoot(), 'sounds');
}

export function modelsDir(): string {
  return path.join(storageRoot(), 'models');
}

export function venvDir(): string {
  return path.join(storageRoot(), 'venv');
}

export function binDir(): string {
  return path.join(storageRoot(), 'bin');
}

export function pidFile(): string {
  return path.join(storageRoot(), 'daemon.pid');
}

export function stateFile(): string {
  return path.join(storageRoot(), 'daemon-state.json');
}

export function piperBin(): string {
  return path.join(binDir(), 'piper');
}

export function piperModelPath(name: string): string {
  const base = name.replace(/\.onnx$/, '');
  return path.join(binDir(), `${base}.onnx`);
}
