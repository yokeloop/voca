import { createInterface } from 'node:readline/promises';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { readConfig, writeConfig, ensureConfigDir, configPath, getAvailableProfiles } from './config.js';
import { run, runCapture, fileExists } from './util.js';
import { installVoice, listAvailable, listInstalled } from './voice.js';
import { binDir, modelsDir, soundsDir, storageRoot, venvDir, writePointerFile } from './paths.js';
import type { VocaConfig } from './types.js';

function defaultRoot(): string {
  return path.join(os.homedir(), '.voca');
}

const PIPER_URL =
  'https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz';
// Universal fallback list used when the HF catalog is unreachable.
const FALLBACK_VOICES = [
  'en_US-lessac-medium',
  'en_US-amy-medium',
  'ru_RU-irina-medium',
  'de_DE-thorsten-medium',
];
const WAKE_MODEL_URL =
  'https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/hey_jarvis_v0.1.onnx';

async function confirm(rl: ReturnType<typeof createInterface>, question: string): Promise<boolean> {
  const answer = await rl.question(`${question} [y/N] `);
  return answer.trim().toLowerCase() === 'y';
}

function select(options: string[], prompt: string): Promise<string> {
  return new Promise((resolve) => {
    let cursor = 0;
    const windowSize = Math.max(3, Math.min(options.length, (process.stdout.rows ?? 20) - 4));
    let winTop = 0;
    let lastRendered = 0;

    const render = () => {
      // Keep cursor inside the visible window.
      if (cursor < winTop) winTop = cursor;
      else if (cursor >= winTop + windowSize) winTop = cursor - windowSize + 1;
      const winBottom = Math.min(winTop + windowSize, options.length);

      // Move up to overwrite the previous render block.
      if (lastRendered > 0) {
        process.stdout.write(`\x1B[${lastRendered}A`);
      }

      const lines: string[] = [];
      if (winTop > 0) lines.push(`    ↑ ${winTop} more`);
      for (let i = winTop; i < winBottom; i++) {
        lines.push(i === cursor ? `  > ${options[i]}` : `    ${options[i]}`);
      }
      const hiddenBelow = options.length - winBottom;
      if (hiddenBelow > 0) lines.push(`    ↓ ${hiddenBelow} more`);

      for (const line of lines) {
        process.stdout.write('\x1B[2K');
        process.stdout.write(`${line}\n`);
      }
      lastRendered = lines.length;
    };

    process.stdout.write(`${prompt}\n`);
    render();

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

  if (opts.field === 'inputDevice') {
    console.log(
      'Input device: PyAudio uses the system default. For explicit override, ' +
      `set "inputDeviceIndex" (integer) in ${configPath()}.`,
    );

    const options: string[] = [DEFAULT_OPTION];
    if (config.inputDevice !== undefined || config.inputDeviceIndex !== undefined) {
      const parts: string[] = [];
      if (config.inputDevice !== undefined) parts.push(`inputDevice=${config.inputDevice}`);
      if (config.inputDeviceIndex !== undefined) parts.push(`inputDeviceIndex=${config.inputDeviceIndex}`);
      options.push(`Keep current: ${parts.join(', ')}`);
    }

    const selected = await select(options, 'Select input device:');
    if (selected === DEFAULT_OPTION) {
      delete config.inputDevice;
      delete config.inputDeviceIndex;
      console.log('Input device: system default');
    } else {
      console.log('Keeping current input device settings');
    }
    return;
  }

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
    console.log('No ALSA devices listed. You can still choose system default.');
  }

  const options: string[] = [DEFAULT_OPTION, ...devices.map((d) => d.label)];
  if (config[opts.field] !== undefined) {
    options.push(`Keep current: ${config[opts.field]}`);
  }

  const selected = await select(options, `Select ${opts.label} device:`);

  if (selected === DEFAULT_OPTION) {
    delete config[opts.field];
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

async function installPiper(
  rl: ReturnType<typeof createInterface>,
  config: VocaConfig,
): Promise<void> {
  console.log('\n=== Step 4: Piper TTS ===');
  const piperDir = binDir();
  const piperExe = path.join(piperDir, 'piper');

  if (await fileExists(piperExe)) {
    console.log('Piper already installed. Skipping.');
  } else {
    if (!(await confirm(rl, 'Piper not found. Download and install?'))) {
      console.log('Skipped.');
      return;
    }

    await fs.mkdir(piperDir, { recursive: true });
    const tarPath = path.join(storageRoot(), 'piper_linux_aarch64.tar.gz');

    console.log('Downloading piper...');
    await run('curl', ['-L', '-o', tarPath, PIPER_URL]);

    console.log('Extracting...');
    await run('tar', ['-xzf', tarPath, '-C', piperDir, '--strip-components=1']);

    await fs.unlink(tarPath).catch(() => {});
    console.log('Piper installed.');
  }

  await selectVoice(rl, config);
}

async function setActiveVoice(config: VocaConfig, name: string): Promise<void> {
  const relative = `bin/${name}.onnx`;
  if (config.piperModel !== relative) {
    config.piperModel = relative;
    await writeConfig(config);
  }
  console.log(`Active voice: ${name}`);
}

function currentVoiceName(config: VocaConfig): string {
  return path.basename(config.piperModel).replace(/\.onnx$/, '');
}

function languageOf(voiceName: string): string {
  return voiceName.split('_')[0].toLowerCase();
}

async function promptLanguage(
  config: VocaConfig,
  available: string[] | null,
): Promise<string | null> {
  const pool = Array.from(
    new Set(available ?? FALLBACK_VOICES.map(languageOf)),
  ).sort();

  const options: string[] = [];
  const current = config.language;
  const KEEP = current ? `${current} (current)` : null;
  if (KEEP) options.push(KEEP);
  for (const lang of pool) {
    if (lang === current) continue;
    options.push(lang);
  }
  const SKIP = 'Skip voice install';
  options.push(SKIP);

  const selected = await select(options, 'Select Piper language:');
  if (selected === SKIP) return null;
  return selected === KEEP ? (current as string) : selected;
}

async function selectVoice(
  _rl: ReturnType<typeof createInterface>,
  config: VocaConfig,
): Promise<void> {
  const current = currentVoiceName(config);
  const installed = await listInstalled();
  const isInstalled = current.length > 0 && installed.includes(current);

  let fullCatalog: Array<{ name: string; langCode: string; quality: string }> | null = null;
  try {
    fullCatalog = await listAvailable({ all: true });
  } catch (err) {
    console.log(`Could not fetch voice catalog (${(err as Error).message}).`);
    console.log('Falling back to a curated list of universal voices.');
  }

  const languages = fullCatalog ? fullCatalog.map((v) => languageOf(v.name)) : null;
  const chosenLanguage = await promptLanguage(config, languages);
  if (chosenLanguage === null) {
    console.log('Skipped voice download.');
    return;
  }

  if (chosenLanguage !== config.language) {
    config.language = chosenLanguage;
    await writeConfig(config);
    console.log(`Language set to: ${chosenLanguage}`);
  }

  const voicesForLang = fullCatalog
    ? fullCatalog.filter((v) => languageOf(v.name) === chosenLanguage)
    : FALLBACK_VOICES.filter((n) => languageOf(n) === chosenLanguage).map((name) => ({
        name,
        langCode: name.split('-')[0],
        quality: name.split('-').pop() ?? 'medium',
      }));

  if (voicesForLang.length === 0) {
    console.log(`No voices available for language "${chosenLanguage}".`);
    return;
  }

  const SKIP = 'Skip — install a voice later via `voca voice install <name>`';
  const nameByOption = new Map<string, string>();
  const options: string[] = [];

  if (current.length > 0 && languageOf(current) === chosenLanguage) {
    const label = `${current} (current)`;
    options.push(label);
    nameByOption.set(label, current);
  }
  for (const v of voicesForLang) {
    if (v.name === current) continue;
    const label = `${v.name} (${v.quality})`;
    options.push(label);
    nameByOption.set(label, v.name);
  }
  options.push(SKIP);

  const selected = await select(options, `Select Piper voice (language: ${chosenLanguage}):`);
  if (selected === SKIP) {
    console.log('Skipped voice download.');
    return;
  }

  const name = nameByOption.get(selected);
  if (!name) {
    console.log('Skipped.');
    return;
  }

  if (name === current && isInstalled) {
    console.log(`Keeping current voice: ${name}`);
    return;
  }

  await installVoice(name);
  await setActiveVoice(config, name);
}

async function installPythonVenv(rl: ReturnType<typeof createInterface>): Promise<void> {
  console.log('\n=== Step 5: Python venv + openWakeWord ===');
  const venv = venvDir();
  const venvPython = path.join(venv, 'bin/python3');

  if (await fileExists(venvPython)) {
    console.log('Python venv already exists. Skipping venv creation.');
  } else {
    if (!(await confirm(rl, 'Python venv not found. Create and install openWakeWord?'))) {
      console.log('Skipped.');
      return;
    }

    console.log('Creating Python venv...');
    await run('python3', ['-m', 'venv', venv]);
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
  const venvPip = path.join(venv, 'bin/pip');
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
  const models = modelsDir();
  const wakeModelPath = path.join(models, 'hey_jarvis_v0.1.onnx');

  if (await fileExists(wakeModelPath)) {
    console.log('Wake model already installed. Skipping.');
    return;
  }

  if (!(await confirm(rl, 'Wake word ONNX model not found. Install?'))) {
    console.log('Skipped.');
    return;
  }

  await fs.mkdir(models, { recursive: true });

  // Primary: copy from installed openwakeword package in venv
  const venvModelPath = path.join(
    venvDir(), 'lib', 'python3.13', 'site-packages',
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
  const sounds = soundsDir();
  await fs.mkdir(sounds, { recursive: true });

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
    const dest = path.join(sounds, file);
    if (await fileExists(src)) {
      await fs.copyFile(src, dest);
      copied++;
    } else {
      console.log(`Warning: ${src} not found, skipping.`);
    }
  }

  console.log(`Copied ${copied} sound file(s) to ${sounds}.`);
}

async function promptStorageRoot(rl: ReturnType<typeof createInterface>): Promise<string> {
  const fallback = defaultRoot();
  for (;;) {
    const raw = (await rl.question(`Enter VOCA storage path [${fallback}] `)).trim();
    const expanded = raw.length === 0
      ? fallback
      : raw.startsWith('~/')
        ? path.join(os.homedir(), raw.slice(2))
        : raw === '~'
          ? os.homedir()
          : raw;
    if (path.isAbsolute(expanded)) return expanded;
    console.log('Path must be absolute — try again.');
  }
}

export async function runBootstrap(): Promise<void> {
  const rl = createInterface({ input: process.stdin, output: process.stdout });

  try {
    console.log('VOCA Bootstrap — Interactive Setup');
    console.log('==================================');

    console.log('\n=== Step 0: Storage root ===');
    const root = await promptStorageRoot(rl);
    await fs.mkdir(root, { recursive: true });
    await writePointerFile(root);
    console.log(`Storage root: ${root}`);

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

    await installPiper(rl, config);
    await installPythonVenv(rl);
    await installModels(rl);
    await copySounds();

    console.log('\n=== Bootstrap complete ===');
    console.log(`Config:  ${configPath()}`);
    console.log(`Data:    ${storageRoot()}`);
  } finally {
    rl.close();
  }
}
