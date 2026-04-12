import { EventEmitter } from 'node:events';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

import type {
  VocaConfig,
  DaemonState,
  AgentResponse,
  ListenerHandle,
} from './types.js';
import { transition } from './daemon-state.js';
import { spawnListener } from './listener.js';
import { playSound } from './sounds.js';
import { transcribe } from './transcriber.js';
import { queryAgent } from './agent.js';
import { speak, type SpeechHandle } from './speaker.js';
import { readSession, incrementMessageCount } from './session.js';
import { readConfig } from './config.js';

export const ASSISTANT_DIR = path.join(os.homedir(), '.openclaw', 'assistant');
export const PID_FILE = path.join(ASSISTANT_DIR, 'daemon.pid');
export const STATE_FILE = path.join(ASSISTANT_DIR, 'daemon-state.json');

export class VocaDaemon extends EventEmitter {
  private state: DaemonState = 'IDLE';
  private listener: ListenerHandle | null = null;
  private config: VocaConfig;
  private activeSpeech: SpeechHandle | null = null;

  constructor(config: VocaConfig) {
    super();
    this.config = config;
  }

  getState(): DaemonState {
    return this.state;
  }

  async start(): Promise<void> {
    const venvPython = path.join(ASSISTANT_DIR, 'venv', 'bin', 'python3');
    let useStub = true;
    try {
      await fs.access(venvPython);
      useStub = false;
    } catch {
      console.log('[daemon] openWakeWord venv not found, using stub listener');
    }

    this.listener = spawnListener({
      stub: useStub,
      pythonBin: useStub ? undefined : venvPython,
      modelDir: path.join(ASSISTANT_DIR, 'models'),
      deviceIndex: useStub ? undefined : 0,
    });

    this.state = 'IDLE';
    this.writeStateFile();
    console.log(`[daemon] state: IDLE — listener spawned (stub=${useStub})`);

    this.listener.on('wake', () => {
      this.handleWake();
    });

    this.listener.on('stop', () => {
      this.handleStop();
    });

    this.listener.on('recorded', (filePath: string) => {
      this.onRecorderDone(filePath);
    });

    this.listener.on('cancelled', () => {
      this.onRecorderCancel();
    });

    this.listener.on('exit', (code) => {
      console.error(`[daemon] listener process exited with code ${code}`);
      if (this.state !== 'IDLE') {
        this.recoverFromError();
      } else {
        this.stop();
      }
    });
  }

  async stop(): Promise<void> {
    if (this.listener) {
      this.listener.kill();
      this.listener = null;
    }
    this.state = 'IDLE';
    this.writeStateFile();
    this.emit('stopped');
    console.log('[daemon] stopped');
  }

  async cleanup(): Promise<void> {
    try { await fs.unlink(PID_FILE); } catch { /* best-effort */ }
    try { await fs.unlink(STATE_FILE); } catch { /* best-effort */ }
  }

  private writeStateFile(): void {
    (async () => {
      try {
        const session = await readSession();
        const config = await readConfig();
        const data = {
          state: this.state,
          sessionId: session.sessionId,
          profile: config.profile,
          updatedAt: new Date().toISOString(),
        };
        await fs.writeFile(STATE_FILE, JSON.stringify(data, null, 2) + '\n');
      } catch {
        // fire-and-forget
      }
    })().catch(() => {});
  }

  private async handleWake(): Promise<void> {
    if (this.state === 'IDLE') {
      try {
        this.state = transition(this.state, 'WAKE');
        this.writeStateFile();
        console.log(`[daemon] state: ${this.state}`);

        await playSound('wake', { device: this.config.outputDevice });

        // Send SIGUSR1 to listener.py to start recording
        this.listener?.pause();

        this.state = transition(this.state, 'START_RECORD');
        this.writeStateFile();
        console.log(`[daemon] state: ${this.state}`);
      } catch (err) {
        console.error('[daemon] error in handleWake:', err);
        await this.recoverFromError();
      }
      return;
    }

    if (this.state === 'SPEAKING') {
      try {
        this.activeSpeech?.interrupt();
        this.state = transition(this.state, 'WAKE_INTERRUPT');
        this.writeStateFile();
        console.log(`[daemon] state: ${this.state} (interrupted TTS)`);
        // Skip wake sound to keep latency under ~200ms.
        this.listener?.pause();
      } catch (err) {
        console.error('[daemon] error in handleWake (interrupt):', err);
        await this.recoverFromError();
      }
      return;
    }

    console.log(`[daemon] ignoring wake in state ${this.state}`);
  }

  private handleStop(): void {
    if (this.state === 'RECORDING') {
      console.log('[daemon] stop phrase detected — stopping recorder');
      this.listener?.resume();
    } else if (this.state === 'LISTENING') {
      this.state = transition(this.state, 'STOP');
      this.writeStateFile();
      console.log(`[daemon] state: ${this.state} (stop during LISTENING)`);
      this.listener?.resume();
    }
  }

  private async onRecorderDone(filePath: string): Promise<void> {
    try {
      this.state = transition(this.state, 'STOP');
      this.writeStateFile();
      console.log(`[daemon] state: ${this.state}`);

      await playSound('stop', { device: this.config.outputDevice });

      if (!filePath) {
        console.error('[daemon] no recorded file path');
        await this.recoverFromError();
        return;
      }

      this.emit('recorded', filePath);

      let text: string;
      try {
        text = await transcribe(filePath, { language: this.config.language });
      } catch (err) {
        console.error('[daemon] transcription error:', err);
        await this.recoverFromError();
        return;
      } finally {
        await fs.unlink(filePath).catch(() => {});
      }

      if (!text) {
        console.log('[daemon] empty transcription — returning to IDLE');
        try {
          this.state = transition(this.state, 'ERROR');
        } catch {
          this.state = 'IDLE';
        }
        this.writeStateFile();
        return;
      }

      console.log(`[daemon] transcript: "${text}"`);

      let response: AgentResponse;
      try {
        const session = await readSession();
        response = await queryAgent({
          agentId: this.config.profile,
          sessionId: session.sessionId,
          message: text,
        });
      } catch (err) {
        console.error('[daemon] agent error:', err);
        await this.recoverFromError();
        return;
      }

      console.log(`[daemon] agent response: "${response.text.slice(0, 100)}..."`);

      this.state = transition(this.state, 'PROCESSING_DONE');
      this.writeStateFile();
      console.log(`[daemon] state: ${this.state}`);

      try {
        await incrementMessageCount();
      } catch (err) {
        console.error('[daemon] failed to increment message count:', err);
        // non-fatal, continue speaking
      }

      this.listener?.speakingStart();
      try {
        this.activeSpeech = speak({
          text: response.text,
          piperBin: this.config.piperBin,
          piperModel: this.config.piperModel,
          device: this.config.outputDevice,
        });
        await this.activeSpeech.done;
      } catch (err) {
        console.error('[daemon] speaker error:', err);
        this.activeSpeech = null;
        this.listener?.speakingEnd();
        await this.recoverFromError();
        return;
      }
      this.activeSpeech = null;
      this.listener?.speakingEnd();

      // If interrupt fired, daemon already transitioned to RECORDING via handleWake.
      if (this.state !== 'SPEAKING') {
        return;
      }

      this.state = transition(this.state, 'SPEAKING_DONE');
      this.writeStateFile();
      console.log(`[daemon] state: ${this.state}`);
    } catch (err) {
      console.error('[daemon] error in onRecorderDone:', err);
      await this.recoverFromError();
    }
  }

  private onRecorderCancel(): void {
    try {
      this.state = transition(this.state, 'RECORD_CANCEL');
      this.writeStateFile();
      console.log(`[daemon] state: ${this.state} (recording cancelled)`);
    } catch (err) {
      console.error('[daemon] error in onRecorderCancel:', err);
      this.recoverFromError();
    }
  }

  private async recoverFromError(): Promise<void> {
    try {
      await playSound('error', { device: this.config.outputDevice });
    } catch {
      // Ignore sound playback errors during recovery
    }
    try {
      this.state = transition(this.state, 'ERROR');
    } catch {
      // If transition fails (e.g. already IDLE), force IDLE
      this.state = 'IDLE';
    }
    this.writeStateFile();
    console.log(`[daemon] state: ${this.state} (recovered from error)`);
    this.listener?.resume();
  }
}
