import { createInterface } from 'node:readline/promises';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { readConfig, writeConfig, ensureConfigDir, CONFIG_PATH } from './config.js';
import type { VocaConfig } from './types.js';

const ASSISTANT_DIR = path.join(os.homedir(), '.openclaw/assistant');
const VENV_DIR = path.join(ASSISTANT_DIR, 'venv');
const PIPER_DIR = path.join(ASSISTANT_DIR, 'bin');
const MODELS_DIR = path.join(ASSISTANT_DIR, 'models');
const SOUNDS_DIR = path.join(ASSISTANT_DIR, 'sounds');

const PIPER_URL =
  'https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz';
const PIPER_VOICE_BASE =
  'https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/irina/medium';
const WAKE_MODEL_URL =
  'https://github.com/dscripka/openWakeWord/releases/download/v0.6.0/hey_jarvis.onnx';
const STOP_MODEL_URL =
  'https://github.com/dscripka/openWakeWord/releases/download/v0.6.0/stop.onnx';

function run(cmd: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { stdio: 'inherit' });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${cmd} exited with code ${code}`));
    });
  });
}

function runCapture(cmd: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '';
    child.stdout.on('data', (d: Buffer) => { stdout += d.toString(); });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(`${cmd} exited with code ${code}`));
    });
  });
}

async function confirm(rl: ReturnType<typeof createInterface>, question: string): Promise<boolean> {
  const answer = await rl.question(`${question} [y/N] `);
  return answer.trim().toLowerCase() === 'y';
}

async function fileExists(p: string): Promise<boolean> {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

async function selectDevice(
  rl: ReturnType<typeof createInterface>,
  config: VocaConfig,
  opts: { step: string; listCmd: string; listArgs: string[]; field: 'inputDevice' | 'outputDevice'; label: string },
): Promise<void> {
  console.log(`\n=== ${opts.step} ===`);
  let output: string;
  try {
    output = await runCapture(opts.listCmd, opts.listArgs);
  } catch {
    console.log(`Could not list devices (${opts.listCmd} ${opts.listArgs.join(' ')} failed). Skipping.`);
    return;
  }

  console.log(output);
  const answer = await rl.question(`Enter ${opts.label} device (current: ${config[opts.field]}): `);
  const trimmed = answer.trim();
  if (trimmed) {
    config[opts.field] = trimmed;
    console.log(`${opts.label} device set to: ${trimmed}`);
  } else {
    console.log(`Keeping current: ${config[opts.field]}`);
  }
}

async function selectProfile(rl: ReturnType<typeof createInterface>, config: VocaConfig): Promise<void> {
  console.log('\n=== Step 3: Select profile ===');
  console.log('Available profiles: personal, public');
  const answer = await rl.question(`Enter profile (current: ${config.profile}): `);
  const trimmed = answer.trim();
  if (trimmed === 'personal' || trimmed === 'public') {
    config.profile = trimmed;
    console.log(`Profile set to: ${trimmed}`);
  } else if (trimmed) {
    console.log(`Invalid profile "${trimmed}". Keeping current: ${config.profile}`);
  } else {
    console.log(`Keeping current: ${config.profile}`);
  }
}

async function installPiper(rl: ReturnType<typeof createInterface>): Promise<void> {
  console.log('\n=== Step 4: Piper TTS ===');
  const piperBin = path.join(PIPER_DIR, 'piper');

  if (await fileExists(piperBin)) {
    console.log('Piper already installed. Skipping.');
  } else {
    if (!(await confirm(rl, 'Piper not found. Download and install?'))) {
      console.log('Skipped.');
      return;
    }

    await fs.mkdir(PIPER_DIR, { recursive: true });
    const tarPath = path.join(ASSISTANT_DIR, 'piper_linux_aarch64.tar.gz');

    console.log('Downloading piper...');
    await run('curl', ['-L', '-o', tarPath, PIPER_URL]);

    console.log('Extracting...');
    await run('tar', ['-xzf', tarPath, '-C', PIPER_DIR, '--strip-components=1']);

    await fs.unlink(tarPath).catch(() => {});
    console.log('Piper installed.');
  }

  // Voice model
  const onnxPath = path.join(PIPER_DIR, 'ru_RU-irina-medium.onnx');
  const jsonPath = path.join(PIPER_DIR, 'ru_RU-irina-medium.onnx.json');

  if (await fileExists(onnxPath)) {
    console.log('Piper voice model already downloaded. Skipping.');
  } else {
    if (!(await confirm(rl, 'Download ru_RU-irina-medium voice model?'))) {
      console.log('Skipped.');
      return;
    }

    console.log('Downloading voice model (.onnx)...');
    await run('curl', ['-L', '-o', onnxPath, `${PIPER_VOICE_BASE}/ru_RU-irina-medium.onnx`]);

    console.log('Downloading voice model config (.onnx.json)...');
    await run('curl', ['-L', '-o', jsonPath, `${PIPER_VOICE_BASE}/ru_RU-irina-medium.onnx.json`]);

    console.log('Voice model downloaded.');
  }
}

async function installPythonVenv(rl: ReturnType<typeof createInterface>): Promise<void> {
  console.log('\n=== Step 5: Python venv + openWakeWord ===');
  const venvPython = path.join(VENV_DIR, 'bin/python3');

  if (await fileExists(venvPython)) {
    console.log('Python venv already exists. Skipping venv creation.');
  } else {
    if (!(await confirm(rl, 'Python venv not found. Create and install openWakeWord?'))) {
      console.log('Skipped.');
      return;
    }

    console.log('Creating Python venv...');
    await run('python3', ['-m', 'venv', VENV_DIR]);
    console.log('Venv created.');
  }

  // Install packages (skip if already present)
  const venvPip = path.join(VENV_DIR, 'bin/pip');
  let alreadyInstalled = false;
  try {
    await runCapture(venvPip, ['show', 'openwakeword']);
    alreadyInstalled = true;
  } catch {
    // not installed
  }

  if (alreadyInstalled) {
    console.log('openwakeword already installed. Skipping pip install.');
  } else {
    console.log('Installing openwakeword and pyaudio...');
    await run(venvPip, ['install', 'openwakeword', 'pyaudio']);
    console.log('Python dependencies installed.');
  }
}

async function installModels(rl: ReturnType<typeof createInterface>): Promise<void> {
  console.log('\n=== Step 6: Wake word ONNX models ===');
  const wakeModelPath = path.join(MODELS_DIR, 'hey_jarvis.onnx');
  const stopModelPath = path.join(MODELS_DIR, 'stop.onnx');

  const wakeExists = await fileExists(wakeModelPath);
  const stopExists = await fileExists(stopModelPath);

  if (wakeExists && stopExists) {
    console.log('Wake and stop models already downloaded. Skipping.');
    return;
  }

  if (!(await confirm(rl, 'Download missing wake/stop word ONNX models?'))) {
    console.log('Skipped.');
    return;
  }

  await fs.mkdir(MODELS_DIR, { recursive: true });

  if (!wakeExists) {
    console.log('Downloading hey_jarvis.onnx...');
    await run('curl', ['-L', '-o', wakeModelPath, WAKE_MODEL_URL]);
    console.log('Wake word model downloaded.');
  }

  if (!stopExists) {
    console.log('Downloading stop.onnx...');
    await run('curl', ['-L', '-o', stopModelPath, STOP_MODEL_URL]);
    console.log('Stop word model downloaded.');
  }
}

async function copySounds(): Promise<void> {
  console.log('\n=== Step 7: Copy sound files ===');
  await fs.mkdir(SOUNDS_DIR, { recursive: true });

  // Resolve sounds dir relative to this source file
  const projectRoot = path.resolve(
    path.dirname(new URL(import.meta.url).pathname),
    '..',
  );
  const srcSounds = path.join(projectRoot, 'sounds');

  const files = ['wake.wav', 'stop.wav', 'error.wav'];
  let copied = 0;

  for (const file of files) {
    const src = path.join(srcSounds, file);
    const dest = path.join(SOUNDS_DIR, file);
    if (await fileExists(src)) {
      await fs.copyFile(src, dest);
      copied++;
    } else {
      console.log(`Warning: ${src} not found, skipping.`);
    }
  }

  console.log(`Copied ${copied} sound file(s) to ${SOUNDS_DIR}.`);
}

export async function runBootstrap(): Promise<void> {
  const rl = createInterface({ input: process.stdin, output: process.stdout });

  try {
    console.log('VOCA Bootstrap — Interactive Setup');
    console.log('==================================');

    await ensureConfigDir();
    const config = await readConfig();

    await selectDevice(rl, config, {
      step: 'Step 1: Select microphone', listCmd: 'arecord', listArgs: ['-l'],
      field: 'inputDevice', label: 'input',
    });
    await selectDevice(rl, config, {
      step: 'Step 2: Select speaker', listCmd: 'aplay', listArgs: ['-l'],
      field: 'outputDevice', label: 'output',
    });
    await selectProfile(rl, config);

    await writeConfig(config);
    console.log('\nConfig saved.');

    await installPiper(rl);
    await installPythonVenv(rl);
    await installModels(rl);
    await copySounds();

    console.log('\n=== Bootstrap complete ===');
    console.log(`Config:  ${CONFIG_PATH}`);
    console.log(`Data:    ${ASSISTANT_DIR}`);
  } finally {
    rl.close();
  }
}
