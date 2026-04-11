import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { defaultConfig, readConfig, writeConfig } from '../src/config.js';

describe('config', () => {
  let tmpDir: string;
  let configPath: string;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'voca-config-test-'));
    configPath = path.join(tmpDir, 'config.json');
  });

  afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  it('readConfig returns defaults when file does not exist', async () => {
    const cfg = await readConfig(configPath);
    expect(cfg).toEqual(defaultConfig);
  });

  it('writeConfig creates file and readConfig reads it back', async () => {
    const custom = { ...defaultConfig, profile: 'public', language: 'en' };
    await writeConfig(custom, configPath);

    const read = await readConfig(configPath);
    expect(read.profile).toBe('public');
    expect(read.language).toBe('en');
    expect(read.inputDevice).toBe(defaultConfig.inputDevice);
  });

  it('readConfig merges partial file over defaults', async () => {
    await fs.writeFile(configPath, JSON.stringify({ profile: 'public' }), 'utf-8');
    const cfg = await readConfig(configPath);

    expect(cfg.profile).toBe('public');
    expect(cfg.wakeWord).toBe(defaultConfig.wakeWord);
    expect(cfg.piperModel).toBe(defaultConfig.piperModel);
  });

  it('writeConfig creates nested directories', async () => {
    const nestedPath = path.join(tmpDir, 'a', 'b', 'config.json');
    await writeConfig(defaultConfig, nestedPath);

    const cfg = await readConfig(nestedPath);
    expect(cfg).toEqual(defaultConfig);
  });

  it('defaultConfig has expected fields', () => {
    expect(defaultConfig.inputDevice).toBe('plughw:2,0');
    expect(defaultConfig.outputDevice).toBe('plughw:2,0');
    expect(defaultConfig.profile).toBe('personal');
    expect(defaultConfig.wakeWord).toBe('hey_jarvis');
    expect(defaultConfig.stopWord).toBe('stop');
    expect(defaultConfig.piperModel).toContain('ru_RU-irina-medium.onnx');
    expect(defaultConfig.piperBin).toContain('.openclaw/assistant/bin/piper');
    expect(defaultConfig.language).toBe('ru');
  });
});
