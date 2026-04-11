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
import { transcribe } from './transcriber.js';
import { queryAgent } from './agent.js';
import { speak } from './speaker.js';
import { readSession, incrementMessageCount } from './session.js';

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

      const filePath = this.recorder?.filePath;
      this.recorder = null;

      if (!filePath) {
        console.error('[daemon] no recorded file path');
        await this.recoverFromError();
        return;
      }

      this.emit('recorded', filePath);

      // Transcribe
      let text: string;
      try {
        text = await transcribe(filePath, { language: this.config.language });
      } catch (err) {
        console.error('[daemon] transcription error:', err);
        await this.recoverFromError();
        return;
      }

      if (!text) {
        console.log('[daemon] empty transcription — returning to IDLE');
        try {
          this.state = transition(this.state, 'ERROR');
        } catch {
          this.state = 'IDLE';
        }
        this.listener?.resume();
        return;
      }

      console.log(`[daemon] transcript: "${text}"`);

      // Query agent
      let response: { text: string; sessionId: string };
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

      // Transition to SPEAKING
      this.state = transition(this.state, 'PROCESSING_DONE');
      console.log(`[daemon] state: ${this.state}`);

      try {
        await incrementMessageCount();
      } catch (err) {
        console.error('[daemon] failed to increment message count:', err);
        // non-fatal, continue speaking
      }

      // Speak response
      try {
        await speak({
          text: response.text,
          piperBin: this.config.piperBin,
          piperModel: this.config.piperModel,
          device: this.config.outputDevice,
        });
      } catch (err) {
        console.error('[daemon] speaker error:', err);
        await this.recoverFromError();
        return;
      }

      // Done speaking — back to IDLE
      this.state = transition(this.state, 'SPEAKING_DONE');
      console.log(`[daemon] state: ${this.state}`);
      this.listener?.resume();
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
