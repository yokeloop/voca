import { describe, it, expect, vi, beforeEach } from 'vitest';
import { EventEmitter } from 'node:events';
import type { ListenerHandle, RecorderHandle, VocaConfig } from '../src/types.js';

/* ------------------------------------------------------------------ */
/*  Mock all I/O modules                                               */
/* ------------------------------------------------------------------ */

// Listener mock — returns an EventEmitter-based handle
const mockListenerHandle = Object.assign(new EventEmitter(), {
  pause: vi.fn(),
  resume: vi.fn(),
  kill: vi.fn(),
}) as EventEmitter & ListenerHandle;

vi.mock('../src/listener.js', () => ({
  spawnListener: vi.fn(() => mockListenerHandle),
}));

// Recorder mock — returns an EventEmitter-based handle
let mockRecorderHandle: EventEmitter & RecorderHandle;
function createMockRecorder(): EventEmitter & RecorderHandle {
  return Object.assign(new EventEmitter(), {
    filePath: '/tmp/voca-rec-test.wav',
    stop: vi.fn(),
    cancel: vi.fn(),
  }) as EventEmitter & RecorderHandle;
}

vi.mock('../src/recorder.js', () => ({
  startRecording: vi.fn(() => mockRecorderHandle),
}));

vi.mock('../src/sounds.js', () => ({
  playSound: vi.fn(async () => {}),
}));

vi.mock('../src/transcriber.js', () => ({
  transcribe: vi.fn(async () => 'hello world'),
}));

vi.mock('../src/agent.js', () => ({
  queryAgent: vi.fn(async () => ({ text: 'agent reply', sessionId: 'asst-123' })),
}));

vi.mock('../src/speaker.js', () => ({
  speak: vi.fn(async () => {}),
}));

vi.mock('../src/session.js', () => ({
  readSession: vi.fn(async () => ({
    sessionId: 'asst-123',
    messageCount: 0,
    profile: 'personal',
    createdAt: '2026-01-01T00:00:00.000Z',
  })),
  incrementMessageCount: vi.fn(async () => {}),
}));

vi.mock('../src/config.js', () => ({
  readConfig: vi.fn(async () => ({
    inputDevice: 'hw:0,0',
    outputDevice: 'hw:0,0',
    profile: 'personal',
    wakeWord: 'hey_jarvis',
    stopWord: 'стоп',
    piperModel: 'ru_RU-irina-medium',
    piperBin: '/usr/bin/piper',
    language: 'ru',
  })),
}));

// Prevent actual file I/O for state file / pid file
vi.mock('node:fs/promises', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs/promises')>();
  return {
    ...actual,
    default: {
      ...actual,
      access: vi.fn(async () => { throw new Error('not found'); }),
      writeFile: vi.fn(async () => {}),
      unlink: vi.fn(async () => {}),
    },
  };
});

/* ------------------------------------------------------------------ */
/*  Import the module under test AFTER mocks are set up                */
/* ------------------------------------------------------------------ */

import { VocaDaemon } from '../src/daemon.js';
import { playSound } from '../src/sounds.js';
import { transcribe } from '../src/transcriber.js';
import { queryAgent } from '../src/agent.js';
import { speak } from '../src/speaker.js';
import { incrementMessageCount } from '../src/session.js';

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const mockConfig: VocaConfig = {
  inputDevice: 'hw:0,0',
  outputDevice: 'hw:0,0',
  profile: 'personal',
  wakeWord: 'hey_jarvis',
  stopWord: 'стоп',
  piperModel: 'ru_RU-irina-medium',
  piperBin: '/usr/bin/piper',
  language: 'ru',
};

/** Flush microtask queue so async callbacks resolve */
function flush(): Promise<void> {
  return new Promise((r) => setTimeout(r, 10));
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe('VocaDaemon', () => {
  let daemon: VocaDaemon;

  beforeEach(() => {
    vi.clearAllMocks();
    // Reset the listener EventEmitter listeners from previous tests
    mockListenerHandle.removeAllListeners();
    // Create a fresh recorder for each test
    mockRecorderHandle = createMockRecorder();
    daemon = new VocaDaemon(mockConfig);
  });

  describe('happy path: wake → record → stop → processing → speaking → idle', () => {
    it('transitions through the full flow', async () => {
      expect(daemon.getState()).toBe('IDLE');

      // Start the daemon — spawns listener, stays IDLE
      await daemon.start();
      expect(daemon.getState()).toBe('IDLE');

      // Simulate wake word detection
      mockListenerHandle.emit('wake');
      await flush();

      // After wake: plays wake sound, starts recording → RECORDING
      expect(playSound).toHaveBeenCalledWith('wake', { device: 'hw:0,0' });
      expect(daemon.getState()).toBe('RECORDING');

      // Simulate recorder finishing (stop phrase or silence detected)
      mockRecorderHandle.emit('done');
      await flush();

      // After done: plays stop sound, transcribes, queries agent, speaks
      expect(playSound).toHaveBeenCalledWith('stop', { device: 'hw:0,0' });
      expect(transcribe).toHaveBeenCalledWith('/tmp/voca-rec-test.wav', { language: 'ru' });
      expect(queryAgent).toHaveBeenCalledWith({
        agentId: 'personal',
        sessionId: 'asst-123',
        message: 'hello world',
      });
      expect(incrementMessageCount).toHaveBeenCalled();
      expect(speak).toHaveBeenCalledWith({
        text: 'agent reply',
        piperBin: '/usr/bin/piper',
        piperModel: 'ru_RU-irina-medium',
        device: 'hw:0,0',
      });

      // Listener should have been paused during speaking, then resumed
      expect(mockListenerHandle.pause).toHaveBeenCalled();
      expect(mockListenerHandle.resume).toHaveBeenCalled();

      // Final state: back to IDLE
      expect(daemon.getState()).toBe('IDLE');
    });
  });

  describe('cancel path: wake → record → cancel → idle', () => {
    it('returns to IDLE when recording is cancelled', async () => {
      await daemon.start();
      expect(daemon.getState()).toBe('IDLE');

      // Wake
      mockListenerHandle.emit('wake');
      await flush();
      expect(daemon.getState()).toBe('RECORDING');

      // Cancel recording (silence >30s or duration >2min)
      mockRecorderHandle.emit('cancel');
      await flush();

      // Should return to IDLE without transcription or agent call
      expect(daemon.getState()).toBe('IDLE');
      expect(transcribe).not.toHaveBeenCalled();
      expect(queryAgent).not.toHaveBeenCalled();
      expect(speak).not.toHaveBeenCalled();

      // Listener should be resumed
      expect(mockListenerHandle.resume).toHaveBeenCalled();
    });
  });

  describe('stop during RECORDING', () => {
    it('calls recorder.stop() when stop phrase detected during recording', async () => {
      await daemon.start();

      mockListenerHandle.emit('wake');
      await flush();
      expect(daemon.getState()).toBe('RECORDING');

      // Stop phrase detected while recording
      mockListenerHandle.emit('stop');
      await flush();

      expect(mockRecorderHandle.stop).toHaveBeenCalled();
    });
  });

  describe('wake ignored when not IDLE', () => {
    it('ignores wake events when already processing', async () => {
      await daemon.start();

      // First wake
      mockListenerHandle.emit('wake');
      await flush();
      expect(daemon.getState()).toBe('RECORDING');

      // Second wake while recording — should be ignored
      mockListenerHandle.emit('wake');
      await flush();
      expect(daemon.getState()).toBe('RECORDING');
    });
  });

  describe('daemon stop', () => {
    it('cleans up listener and recorder on stop', async () => {
      await daemon.start();

      mockListenerHandle.emit('wake');
      await flush();
      expect(daemon.getState()).toBe('RECORDING');

      await daemon.stop();
      expect(daemon.getState()).toBe('IDLE');
      expect(mockListenerHandle.kill).toHaveBeenCalled();
      expect(mockRecorderHandle.cancel).toHaveBeenCalled();
    });
  });
});
