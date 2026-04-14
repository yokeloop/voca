import { describe, it, expect, vi, beforeEach } from 'vitest';
import { EventEmitter } from 'node:events';
import type { ListenerHandle, VocaConfig } from '../src/types.js';

/* ------------------------------------------------------------------ */
/*  Mock all I/O modules                                               */
/* ------------------------------------------------------------------ */

// Listener mock — returns an EventEmitter-based handle
const mockListenerHandle = Object.assign(new EventEmitter(), {
  pause: vi.fn(),
  resume: vi.fn(),
  speakingStart: vi.fn(),
  speakingEnd: vi.fn(),
  kill: vi.fn(),
}) as EventEmitter & ListenerHandle;

const mockInterrupt = vi.fn();

vi.mock('../src/listener.js', () => ({
  spawnListener: vi.fn(() => mockListenerHandle),
}));

// Recorder mock — kept for import compatibility but no longer used by daemon
vi.mock('../src/recorder.js', () => ({
  startRecording: vi.fn(),
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
  speak: vi.fn(() => ({ done: Promise.resolve(), interrupt: mockInterrupt })),
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
import { spawnListener } from '../src/listener.js';
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
    // Reset speak mock to default (resolved done)
    (speak as any).mockImplementation(() => ({
      done: Promise.resolve(),
      interrupt: mockInterrupt,
    }));
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

      // After wake: plays wake sound, sends SIGUSR1 (pause) to start recording → RECORDING
      expect(playSound).toHaveBeenCalledWith('wake', { device: 'hw:0,0' });
      expect(mockListenerHandle.pause).toHaveBeenCalled();
      expect(daemon.getState()).toBe('RECORDING');

      // Simulate listener.py finishing recording and emitting 'recorded' event
      mockListenerHandle.emit('recorded', '/tmp/voca-rec-test.wav');
      await flush();

      // After recorded: plays stop sound, transcribes, queries agent, speaks
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

      // Cancel recording (silence >30s or duration >2min) — emitted by listener.py
      mockListenerHandle.emit('cancelled');
      await flush();

      // Should return to IDLE without transcription or agent call
      expect(daemon.getState()).toBe('IDLE');
      expect(transcribe).not.toHaveBeenCalled();
      expect(queryAgent).not.toHaveBeenCalled();
      expect(speak).not.toHaveBeenCalled();
    });
  });

  describe('stop during RECORDING', () => {
    it('sends SIGUSR2 (resume) to stop recording when stop phrase detected', async () => {
      await daemon.start();

      mockListenerHandle.emit('wake');
      await flush();
      expect(daemon.getState()).toBe('RECORDING');

      // Stop phrase detected while recording — daemon sends SIGUSR2
      mockListenerHandle.emit('stop');
      await flush();

      expect(mockListenerHandle.resume).toHaveBeenCalled();
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

  describe('interrupt TTS during SPEAKING', () => {
    it('kills speak and transitions to RECORDING on wake during SPEAKING', async () => {
      // Make speak pend so daemon stays in SPEAKING
      let resolveDone!: () => void;
      (speak as any).mockImplementation(() => ({
        done: new Promise<void>((r) => { resolveDone = r; }),
        interrupt: mockInterrupt,
      }));

      await daemon.start();
      mockListenerHandle.emit('wake');
      await flush();
      mockListenerHandle.emit('recorded', '/tmp/voca-rec-test.wav');
      await flush();

      // Now in SPEAKING, speak() pending
      expect(daemon.getState()).toBe('SPEAKING');
      expect(mockListenerHandle.speakingStart).toHaveBeenCalled();

      (playSound as any).mockClear();
      const pauseCallsBefore = (mockListenerHandle.pause as any).mock.calls.length;

      // Second wake during SPEAKING
      mockListenerHandle.emit('wake');
      await flush();

      expect(mockInterrupt).toHaveBeenCalled();
      expect(daemon.getState()).toBe('RECORDING');
      // No wake beep on interrupt
      expect(playSound).not.toHaveBeenCalledWith('wake', expect.anything());
      // SIGUSR1 re-sent to start recording
      expect((mockListenerHandle.pause as any).mock.calls.length).toBeGreaterThan(pauseCallsBefore);

      // Let the pending done resolve so onRecorderDone can finish cleanly
      resolveDone();
      await flush();
      expect(mockListenerHandle.speakingEnd).toHaveBeenCalled();
    });

    it('calls speakingEnd even when speak rejects', async () => {
      (speak as any).mockImplementation(() => ({
        done: Promise.reject(new Error('piper died')),
        interrupt: vi.fn(),
      }));

      await daemon.start();
      mockListenerHandle.emit('wake');
      await flush();
      mockListenerHandle.emit('recorded', '/tmp/voca-rec-test.wav');
      await flush();

      expect(mockListenerHandle.speakingStart).toHaveBeenCalled();
      expect(mockListenerHandle.speakingEnd).toHaveBeenCalled();
      expect(daemon.getState()).toBe('IDLE');
    });
  });

  describe('daemon stop', () => {
    it('cleans up listener on stop', async () => {
      await daemon.start();

      mockListenerHandle.emit('wake');
      await flush();
      expect(daemon.getState()).toBe('RECORDING');

      await daemon.stop();
      expect(daemon.getState()).toBe('IDLE');
      expect(mockListenerHandle.kill).toHaveBeenCalled();
    });
  });
});

describe('VocaDaemon with default devices (no config fields)', () => {
  const mockConfigDefault: VocaConfig = {
    profile: 'personal',
    wakeWord: 'hey_jarvis',
    stopWord: 'стоп',
    piperModel: 'ru_RU-irina-medium',
    piperBin: '/usr/bin/piper',
    language: 'ru',
  };

  let daemon: VocaDaemon;

  beforeEach(() => {
    vi.clearAllMocks();
    mockListenerHandle.removeAllListeners();
    daemon = new VocaDaemon(mockConfigDefault);
  });

  it('spawns listener without deviceIndex and plays sounds without device', async () => {
    await daemon.start();

    // spawnListener received deviceIndex: undefined
    const spawnCall = vi.mocked(spawnListener).mock.calls[0][0];
    expect(spawnCall.deviceIndex).toBeUndefined();

    // Simulate full flow.
    // Note: argv coverage for aplay/-D lives in test/speaker.test.ts and
    // test/sounds.test.ts. The assertions below verify that the `device`
    // property is literally the JS `undefined` value (not a stringified
    // "undefined") and that the key is present in the prop bag — which
    // catches a stringification regression at the daemon layer.
    mockListenerHandle.emit('wake');
    await flush();
    const wakePlayArgs = vi.mocked(playSound).mock.calls[0];
    expect(wakePlayArgs[0]).toBe('wake');
    expect('device' in (wakePlayArgs[1] as object)).toBe(true);
    expect((wakePlayArgs[1] as { device: unknown }).device).toBeUndefined();

    mockListenerHandle.emit('recorded', '/tmp/voca-rec-test.wav');
    await flush();
    const stopPlayArgs = vi.mocked(playSound).mock.calls[1];
    expect(stopPlayArgs[0]).toBe('stop');
    expect('device' in (stopPlayArgs[1] as object)).toBe(true);
    expect((stopPlayArgs[1] as { device: unknown }).device).toBeUndefined();

    const speakArgs = vi.mocked(speak).mock.calls[0][0];
    expect('device' in (speakArgs as object)).toBe(true);
    expect((speakArgs as { device: unknown }).device).toBeUndefined();
  });
});
