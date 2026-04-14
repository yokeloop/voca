import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { storageRoot, readPointerFile, writePointerFile } from '../src/paths.js';

describe('paths', () => {
  let tmpDir: string;
  let prevEnv: string | undefined;
  let prevHome: string | undefined;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'voca-paths-test-'));
    prevEnv = process.env.VOCA_HOME;
    prevHome = process.env.HOME;
    delete process.env.VOCA_HOME;
    process.env.HOME = tmpDir;
  });

  afterEach(async () => {
    if (prevEnv === undefined) delete process.env.VOCA_HOME;
    else process.env.VOCA_HOME = prevEnv;
    if (prevHome === undefined) delete process.env.HOME;
    else process.env.HOME = prevHome;
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  it('VOCA_HOME takes precedence over the pointer file', async () => {
    await writePointerFile(path.join(tmpDir, 'pointed'));
    process.env.VOCA_HOME = path.join(tmpDir, 'from-env');
    expect(storageRoot()).toBe(path.join(tmpDir, 'from-env'));
  });

  it('reads the pointer file when VOCA_HOME is unset', async () => {
    const target = path.join(tmpDir, 'pointed');
    await writePointerFile(target);
    expect(storageRoot()).toBe(target);
  });

  it('throws when both VOCA_HOME and the pointer file are missing', () => {
    expect(() => storageRoot()).toThrow(/run 'voca bootstrap'/);
  });

  it('writePointerFile round-trips and creates ~/.config/voca/ recursively', async () => {
    const target = path.join(tmpDir, 'root');
    await writePointerFile(target);
    const pointerPath = path.join(tmpDir, '.config', 'voca', 'root');
    const contents = await fs.readFile(pointerPath, 'utf-8');
    expect(contents.trim()).toBe(target);
    expect(readPointerFile()).toBe(target);
  });

  it('writePointerFile rejects relative paths', async () => {
    await expect(writePointerFile('relative/path')).rejects.toThrow(/absolute/);
  });
});
