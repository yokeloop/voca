import { createInterface } from 'node:readline/promises';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { readConfig, writeConfig, ensureConfigDir, CONFIG_PATH, getAvailableProfiles } from './config.js';
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
  'https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/hey_jarvis_v0.1.onnx';

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

function select(options: string[], prompt: string): Promise<string> {
  return new Promise((resolve) => {
    let cursor = 0;

    const render = () => {
      // Move up to overwrite previous render (skip on first render)
      if (rendered) {
        process.stdout.write(`\x1B[${options.length}A`);
      }
      for (let i = 0; i < options.length; i++) {
        process.stdout.write('\x1B[2K');
        if (i === cursor) {
          process.stdout.write(`  > ${options[i]}\n`);
        } else {
          process.stdout.write(`    ${options[i]}\n`);
        }
      }
    };

    let rendered = false;
    process.stdout.write(`${prompt}\n`);
    render();
    rendered = true;

    const wasRaw = process.stdin.isRaw;
    const wasPaused = process.stdin.isPaused();
    process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdin.setEncoding('utf8');

    const onData = (key: string) => {
      // Ctrl+C
      if (key === '\x03') {
        cleanup();
        process.exit(0);
      }
      // Enter
      if (key === '\r' || key === '\n') {
        cleanup();
        resolve(options[cursor]);
        return;
      }
      // Arrow Up: \x1B[A or k
      if (key === '\x1B[A' || key === 'k') {
        cursor = (cursor - 1 + options.length) % options.length;
        render();
        return;
      }
      // Arrow Down: \x1B[B or j
      if (key === '\x1B[B' || key === 'j') {
        cursor = (cursor + 1) % options.length;
        render();
        return;
      }
    };

    const cleanup = () => {
      process.stdin.removeListener('data', onData);
      process.stdin.setRawMode(wasRaw ?? false);
      if (wasPaused) process.stdin.pause();
    };

    process.stdin.on('data', onData);
  });
}

interface ParsedDevice {
  alsa: string;
  name: string;
  label: string;
}

function parseDeviceList(output: string): ParsedDevice[] {
  const devices: ParsedDevice[] = [];
  const re = /^card\s+(\d+):\s+\w+\s+\[([^\]]+)\],\s+device\s+(\d+):/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(output)) !== null) {
    const card = m[1];
    const name = m[2];
    const dev = m[3];
    const alsa = `plughw:${card},${dev}`;
    devices.push({ alsa, name, label: `${alsa} — ${name}` });
  }
  return devices;
}

const DEFAULT_OPTION = 'Use system default (recommended)';

async function selectDevice(
  config: VocaConfig,
  opts: { step: string; listCmd: string; listArgs: string[]; field: 'inputDevice' | 'outputDevice'; label: string },
): Promise<void> {
  console.log(`\n=== ${opts.step} ===`);
  console.log('Note: ALSA plughw:X,Y indices may shift after reboot or USB re-plug. "Use system default" survives that.');
  let output: string;
  try {
    output = await runCapture(opts.listCmd, opts.listArgs);
  } catch {
    console.log(`Could not list devices (${opts.listCmd} ${opts.listArgs.join(' ')} failed). Skipping.`);
    return;
  }

  const devices = parseDeviceList(output);
  if (devices.length === 0) {
    console.log('No devices found. Skipping.');
    return;
  }

  const options = [DEFAULT_OPTION, ...devices.map((d) => d.label)];
  if (config[opts.field] !== undefined) {
    options.push(`Keep current: ${config[opts.field]}`);
  }

  const selected = await select(options, `Select ${opts.label} device:`);

  if (selected === DEFAULT_OPTION) {
    delete config[opts.field];
    if (opts.field === 'inputDevice') {
      delete config.inputDeviceIndex;
    }
    console.log(`${opts.label} device: system default`);
    return;
  }

  if (selected.startsWith('Keep current:')) {
    console.log(`Keeping current: ${config[opts.field]}`);
    return;
  }

  const device = devices.find((d) => d.label === selected);
  if (device) {
    config[opts.field] = device.alsa;
    console.log(`${opts.label} device set to: ${device.alsa}`);
  }
}

async function selectProfile(config: VocaConfig): Promise<void> {
  console.log('\n=== Step 3: Select profile ===');
  const options = await getAvailableProfiles();
  const selected = await select(options, `Select profile (current: ${config.profile}):`);
  config.profile = selected;
  console.log(`Profile set to: ${selected}`);
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

  // Ensure portaudio19-dev is installed (required for pyaudio compilation)
  let portaudioInstalled = false;
  try {
    await runCapture('dpkg', ['-s', 'portaudio19-dev']);
    portaudioInstalled = true;
  } catch {
    // not installed
  }

  if (portaudioInstalled) {
    console.log('portaudio19-dev already installed. Skipping.');
  } else {
    if (await confirm(rl, 'portaudio19-dev not found (required for pyaudio). Install via apt?')) {
      await run('sudo', ['apt-get', 'install', '-y', 'portaudio19-dev']);
      console.log('portaudio19-dev installed.');
    } else {
      console.log('Skipped.');
    }
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
  const wakeModelPath = path.join(MODELS_DIR, 'hey_jarvis_v0.1.onnx');

  if (await fileExists(wakeModelPath)) {
    console.log('Wake model already installed. Skipping.');
    return;
  }

  if (!(await confirm(rl, 'Wake word ONNX model not found. Install?'))) {
    console.log('Skipped.');
    return;
  }

  await fs.mkdir(MODELS_DIR, { recursive: true });

  // Primary: copy from installed openwakeword package in venv
  const venvModelPath = path.join(
    ASSISTANT_DIR, 'venv', 'lib', 'python3.13', 'site-packages',
    'openwakeword', 'resources', 'models', 'hey_jarvis_v0.1.onnx',
  );

  if (await fileExists(venvModelPath)) {
    console.log('Copying hey_jarvis_v0.1.onnx from venv...');
    await fs.copyFile(venvModelPath, wakeModelPath);
    console.log('Wake word model installed from venv.');
  } else {
    // Fallback: download from GitHub
    console.log('Downloading hey_jarvis_v0.1.onnx...');
    await run('curl', ['--fail', '-L', '-o', wakeModelPath, WAKE_MODEL_URL]);
    console.log('Wake word model downloaded.');
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

    await selectDevice(config, {
      step: 'Step 1: Select microphone', listCmd: 'arecord', listArgs: ['-l'],
      field: 'inputDevice', label: 'input',
    });
    await selectDevice(config, {
      step: 'Step 2: Select speaker', listCmd: 'aplay', listArgs: ['-l'],
      field: 'outputDevice', label: 'output',
    });
    await selectProfile(config);

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
