import { describe, it, expect, vi, beforeEach } from 'vitest';
import { EventEmitter } from 'node:events';
import { queryAgent, AgentError, OPENCLAW_BIN } from '../src/agent.js';

vi.mock('node:child_process', () => ({
  spawn: vi.fn(),
}));

import { spawn } from 'node:child_process';

const mockSpawn = vi.mocked(spawn);

function createMockChild() {
  const child = new EventEmitter() as any;
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.stdin = { end: vi.fn() };
  return child;
}

function setupSpawn(stdout: string, code = 0) {
  const child = createMockChild();
  mockSpawn.mockReturnValue(child as any);
  // Emit data and close on next tick
  process.nextTick(() => {
    if (stdout) child.stdout.emit('data', Buffer.from(stdout));
    child.emit('close', code);
  });
  return child;
}

function setupSpawnError(errorMsg: string) {
  const child = createMockChild();
  mockSpawn.mockReturnValue(child as any);
  process.nextTick(() => {
    child.emit('error', new Error(errorMsg));
  });
  return child;
}

describe('agent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Suppress console output in tests
    vi.spyOn(console, 'log').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('returns AgentResponse for valid JSON (payloads format)', async () => {
    const json = JSON.stringify({
      result: { payloads: [{ text: 'Hello world', mediaUrl: null }] },
      sessionId: 'asst-123',
    });
    setupSpawn(json);

    const result = await queryAgent({
      agentId: 'personal',
      sessionId: 'asst-123',
      message: 'hi',
    });

    expect(result).toEqual({ text: 'Hello world', sessionId: 'asst-123' });
  });

  it('returns AgentResponse for legacy flat format', async () => {
    const json = JSON.stringify({ text: 'Hello world', sessionId: 'asst-123' });
    setupSpawn(json);

    const result = await queryAgent({
      agentId: 'personal',
      sessionId: 'asst-123',
      message: 'hi',
    });

    expect(result).toEqual({ text: 'Hello world', sessionId: 'asst-123' });
  });

  it('rejects when agent returns empty text', async () => {
    const json = JSON.stringify({ result: { payloads: [] } });
    setupSpawn(json);

    await expect(
      queryAgent({ agentId: 'personal', sessionId: 'asst-1', message: 'hi' }),
    ).rejects.toThrow('empty response');
  });

  it('throws AgentError for non-JSON stdout', async () => {
    setupSpawn('this is not json');

    await expect(
      queryAgent({ agentId: 'personal', sessionId: 'asst-1', message: 'hi' }),
    ).rejects.toThrow(AgentError);
  });

  it('throws AgentError on spawn error', async () => {
    setupSpawnError('agent not found');

    await expect(
      queryAgent({ agentId: 'personal', sessionId: 'asst-1', message: 'hi' }),
    ).rejects.toThrow(AgentError);
  });

  it('throws AgentError on non-zero exit code', async () => {
    setupSpawn('', 1);

    await expect(
      queryAgent({ agentId: 'personal', sessionId: 'asst-1', message: 'hi' }),
    ).rejects.toThrow(AgentError);
  });

  it('passes correct argv to spawn', async () => {
    const json = JSON.stringify({ result: { payloads: [{ text: 'ok' }] } });
    setupSpawn(json);

    await queryAgent({
      agentId: 'public',
      sessionId: 'asst-999',
      message: 'what time is it',
      timeoutS: 120,
    });

    expect(mockSpawn).toHaveBeenCalledWith(
      OPENCLAW_BIN,
      [
        'agent',
        '--agent', 'public',
        '--session-id', 'asst-999',
        '--message', 'what time is it',
        '--json',
        '--timeout', '120',
      ],
      expect.objectContaining({
        timeout: 120 * 1000 + 5000,
      }),
    );
  });

  it('uses default timeout of 900s', async () => {
    const json = JSON.stringify({ result: { payloads: [{ text: 'ok' }] } });
    setupSpawn(json);

    await queryAgent({
      agentId: 'personal',
      sessionId: 'asst-1',
      message: 'hello',
    });

    expect(mockSpawn).toHaveBeenCalledWith(
      expect.any(String),
      expect.arrayContaining(['--timeout', '900']),
      expect.objectContaining({ timeout: 900 * 1000 + 5000 }),
    );
  });

  it('uses sessionId from opts when response lacks it', async () => {
    const json = JSON.stringify({ result: { payloads: [{ text: 'response without sessionId' }] } });
    setupSpawn(json);

    const result = await queryAgent({
      agentId: 'personal',
      sessionId: 'asst-fallback',
      message: 'hi',
    });

    expect(result.sessionId).toBe('asst-fallback');
  });
});
