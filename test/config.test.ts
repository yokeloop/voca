import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { defaultConfig, readConfig, writeConfig, resolvePiperBin, resolvePiperModel } from '../src/config.js';

describe('config', () => {
  let tmpDir: string;
  let configPath: string;
  let prevEnv: string | undefined;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'voca-config-test-'));
    configPath = path.join(tmpDir, 'config.json');
    prevEnv = process.env.VOCA_HOME;
    process.env.VOCA_HOME = tmpDir;
  });

  afterEach(async () => {
    if (prevEnv === undefined) delete process.env.VOCA_HOME;
    else process.env.VOCA_HOME = prevEnv;
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  it('readConfig returns defaults when file does not exist', async () => {
    const cfg = await readConfig(configPath);
    expect(cfg.profile).toBe(defaultConfig.profile);
    expect(cfg.wakeWord).toBe(defaultConfig.wakeWord);
    expect(cfg.stopWord).toBe(defaultConfig.stopWord);
    expect(cfg.language).toBeUndefined();
  });

  it('writeConfig creates file and readConfig reads it back', async () => {
    const custom = { ...defaultConfig, profile: 'public', language: 'en' };
    await writeConfig(custom, configPath);

    const read = await readConfig(configPath);
    expect(read.profile).toBe('public');
    expect(read.language).toBe('en');
  });

  it('readConfig merges partial file over defaults', async () => {
    await fs.writeFile(configPath, JSON.stringify({ profile: 'public' }), 'utf-8');
    const cfg = await readConfig(configPath);

    expect(cfg.profile).toBe('public');
    expect(cfg.wakeWord).toBe(defaultConfig.wakeWord);
    expect(cfg.piperModel).toBe('bin/ru_RU-irina-medium.onnx');
  });

  it('writeConfig creates nested directories', async () => {
    const nestedPath = path.join(tmpDir, 'a', 'b', 'config.json');
    await writeConfig(defaultConfig, nestedPath);

    const cfg = await readConfig(nestedPath);
    expect(cfg.profile).toBe(defaultConfig.profile);
    expect(cfg.wakeWord).toBe(defaultConfig.wakeWord);
  });

  it('defaultConfig has expected fields', () => {
    expect(defaultConfig.inputDevice).toBeUndefined();
    expect(defaultConfig.outputDevice).toBeUndefined();
    expect(defaultConfig.profile).toBe('personal');
    expect(defaultConfig.wakeWord).toBe('hey_jarvis');
    expect(defaultConfig.stopWord).toBe('stop');
    expect(defaultConfig.piperModel).toBe('bin/ru_RU-irina-medium.onnx');
    expect(defaultConfig.piperBin).toBe('bin/piper');
    expect(defaultConfig.language).toBeUndefined();
  });

  it('readConfig preserves relative piperBin; resolvePiperBin returns absolute', async () => {
    await fs.writeFile(configPath, JSON.stringify({ piperBin: 'bin/piper' }), 'utf-8');
    const cfg = await readConfig(configPath);
    expect(cfg.piperBin).toBe('bin/piper');
    expect(resolvePiperBin(cfg)).toBe(path.join(tmpDir, 'bin', 'piper'));
  });

  it('writeConfig round-trip keeps piperBin/piperModel relative', async () => {
    await writeConfig(defaultConfig, configPath);
    const raw = await fs.readFile(configPath, 'utf-8');
    const parsed = JSON.parse(raw);
    expect(parsed.piperBin).toBe('bin/piper');
    expect(parsed.piperModel).toBe('bin/ru_RU-irina-medium.onnx');
  });

  it('resolvePiperModel passes through absolute paths', async () => {
    const abs = '/opt/voices/custom.onnx';
    expect(resolvePiperModel({ ...defaultConfig, piperModel: abs })).toBe(abs);
  });

  it('readConfig returns undefined device fields when file is empty', async () => {
    await fs.writeFile(configPath, JSON.stringify({}), 'utf-8');
    const cfg = await readConfig(configPath);
    expect(cfg.inputDevice).toBeUndefined();
    expect(cfg.outputDevice).toBeUndefined();
    expect(cfg.inputDeviceIndex).toBeUndefined();
  });

  it('readConfig preserves inputDeviceIndex from JSON', async () => {
    await fs.writeFile(configPath, JSON.stringify({ inputDeviceIndex: 3 }), 'utf-8');
    const cfg = await readConfig(configPath);
    expect(cfg.inputDeviceIndex).toBe(3);
  });

  it('defaultConfig JSON omits device fields', () => {
    const serialized = JSON.parse(JSON.stringify(defaultConfig));
    expect('inputDevice' in serialized).toBe(false);
    expect('outputDevice' in serialized).toBe(false);
    expect('inputDeviceIndex' in serialized).toBe(false);
  });
});
