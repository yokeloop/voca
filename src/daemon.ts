import { EventEmitter } from 'node:events';
import path from 'node:path';
import os from 'node:os';

import type {
  VocaConfig,
  DaemonState,
  ListenerHandle,
  RecorderHandle,
} from './types.js';
import { transition } from './daemon-state.js';
import { spawnListener } from './listener.js';
import { startRecording } from './recorder.js';
import { playWake, playStop, playError } from './sounds.js';

const ASSISTANT_DIR = path.join(os.homedir(), '.openclaw', 'assistant');

export class VocaDaemon extends EventEmitter {
  private state: DaemonState = 'IDLE';
  private listener: ListenerHandle | null = null;
  private recorder: RecorderHandle | null = null;
  private config: VocaConfig;

  constructor(config: VocaConfig) {
    super();
    this.config = config;
  }

  getState(): DaemonState {
    return this.state;
  }

  async start(): Promise<void> {
    this.listener = spawnListener({
      stub: true,
      modelDir: path.join(ASSISTANT_DIR, 'models'),
    });

    this.state = 'IDLE';
    console.log('[daemon] state: IDLE — listener spawned');

    this.listener.on('wake', () => {
      this.handleWake();
    });

    this.listener.on('stop', () => {
      this.handleStop();
    });
  }

  async stop(): Promise<void> {
    if (this.recorder) {
      this.recorder.cancel();
      this.recorder = null;
    }
    if (this.listener) {
      this.listener.kill();
      this.listener = null;
    }
    this.state = 'IDLE';
    this.emit('stopped');
    console.log('[daemon] stopped');
  }

  private async handleWake(): Promise<void> {
    if (this.state !== 'IDLE') {
      console.log(`[daemon] ignoring wake in state ${this.state}`);
      return;
    }

    try {
      this.state = transition(this.state, 'WAKE');
      console.log(`[daemon] state: ${this.state}`);

      this.listener!.pause();

      await playWake({ device: this.config.outputDevice });

      this.state = transition(this.state, 'START_RECORD');
      console.log(`[daemon] state: ${this.state}`);

      this.recorder = startRecording({ device: this.config.inputDevice });

      this.recorder.on('done', () => {
        this.onRecorderDone();
      });

      this.recorder.on('cancel', () => {
        this.onRecorderCancel();
      });
    } catch (err) {
      console.error('[daemon] error in handleWake:', err);
      await this.recoverFromError();
    }
  }

  private handleStop(): void {
    if (this.state === 'RECORDING' && this.recorder) {
      console.log('[daemon] stop phrase detected — stopping recorder');
      this.recorder.stop();
    } else if (this.state === 'LISTENING') {
      this.state = transition(this.state, 'STOP');
      console.log(`[daemon] state: ${this.state} (stop during LISTENING)`);
      this.listener?.resume();
    }
  }

  private async onRecorderDone(): Promise<void> {
    try {
      this.state = transition(this.state, 'STOP');
      console.log(`[daemon] state: ${this.state}`);

      await playStop({ device: this.config.outputDevice });

      // TODO(Task 12b): add processing pipeline here
      // — transcribe the recorded audio (this.recorder!.filePath)
      // — send transcript to openclaw agent
      // — speak the response
      // — transition PROCESSING_DONE → SPEAKING → SPEAKING_DONE → IDLE
      // For now, emit 'recorded' with the file path for downstream handling
      const filePath = this.recorder?.filePath;
      this.recorder = null;
      this.emit('recorded', filePath);
    } catch (err) {
      console.error('[daemon] error in onRecorderDone:', err);
      await this.recoverFromError();
    }
  }

  private onRecorderCancel(): void {
    try {
      this.state = transition(this.state, 'RECORD_CANCEL');
      console.log(`[daemon] state: ${this.state} (recording cancelled)`);
      this.recorder = null;
      this.listener?.resume();
    } catch (err) {
      console.error('[daemon] error in onRecorderCancel:', err);
      this.recoverFromError();
    }
  }

  private async recoverFromError(): Promise<void> {
    try {
      await playError({ device: this.config.outputDevice });
    } catch {
      // Ignore sound playback errors during recovery
    }
    try {
      this.state = transition(this.state, 'ERROR');
    } catch {
      // If transition fails (e.g. already IDLE), force IDLE
      this.state = 'IDLE';
    }
    console.log(`[daemon] state: ${this.state} (recovered from error)`);
    this.recorder = null;
    this.listener?.resume();
  }
}
