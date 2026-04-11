import { describe, it, expect, vi, beforeEach } from 'vitest';
import { queryAgent, AgentError } from '../src/agent.js';

vi.mock('node:child_process', () => ({
  execFile: vi.fn(),
}));

import { execFile } from 'node:child_process';

const mockExecFile = vi.mocked(execFile);

function setupExecFile(stdout: string, stderr = '') {
  mockExecFile.mockImplementation((_file, _args, _opts, callback?) => {
    // promisify calls execFile with (file, args, opts, callback)
    const cb = typeof _opts === 'function' ? _opts : callback;
    if (cb) (cb as Function)(null, { stdout, stderr });
    return {} as ReturnType<typeof execFile>;
  });
}

function setupExecFileError(stderr: string, code = 1) {
  mockExecFile.mockImplementation((_file, _args, _opts, callback?) => {
    const cb = typeof _opts === 'function' ? _opts : callback;
    const err = Object.assign(new Error('command failed'), { stderr, code });
    if (cb) (cb as Function)(err, { stdout: '', stderr });
    return {} as ReturnType<typeof execFile>;
  });
}

describe('agent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns AgentResponse for valid JSON', async () => {
    const json = JSON.stringify({ text: 'Hello world', sessionId: 'asst-123' });
    setupExecFile(json);

    const result = await queryAgent({
      agentId: 'personal',
      sessionId: 'asst-123',
      message: 'hi',
    });

    expect(result).toEqual({ text: 'Hello world', sessionId: 'asst-123' });
  });

  it('throws AgentError for non-JSON stdout', async () => {
    setupExecFile('this is not json');

    await expect(
      queryAgent({ agentId: 'personal', sessionId: 'asst-1', message: 'hi' }),
    ).rejects.toThrow(AgentError);

    await expect(
      queryAgent({ agentId: 'personal', sessionId: 'asst-1', message: 'hi' }),
    ).rejects.toThrow(/invalid response/);
  });

  it('throws AgentError on execFile error with stderr', async () => {
    setupExecFileError('agent not found');

    await expect(
      queryAgent({ agentId: 'personal', sessionId: 'asst-1', message: 'hi' }),
    ).rejects.toThrow(AgentError);

    await expect(
      queryAgent({ agentId: 'personal', sessionId: 'asst-1', message: 'hi' }),
    ).rejects.toThrow('agent not found');
  });

  it('passes correct argv to execFile', async () => {
    const json = JSON.stringify({ text: 'ok' });
    setupExecFile(json);

    await queryAgent({
      agentId: 'public',
      sessionId: 'asst-999',
      message: 'what time is it',
      timeoutS: 120,
    });

    expect(mockExecFile).toHaveBeenCalledWith(
      '/home/priney/.npm-global/bin/openclaw',
      [
        'agent',
        '--agent', 'public',
        '--session-id', 'asst-999',
        '--message', 'what time is it',
        '--json',
        '--timeout', '120',
      ],
      {
        maxBuffer: 10 * 1024 * 1024,
        timeout: 120 * 1000 + 5000,
      },
      expect.any(Function),
    );
  });

  it('uses default timeout of 900s', async () => {
    const json = JSON.stringify({ text: 'ok' });
    setupExecFile(json);

    await queryAgent({
      agentId: 'personal',
      sessionId: 'asst-1',
      message: 'hello',
    });

    expect(mockExecFile).toHaveBeenCalledWith(
      expect.any(String),
      expect.arrayContaining(['--timeout', '900']),
      expect.objectContaining({ timeout: 900 * 1000 + 5000 }),
      expect.any(Function),
    );
  });

  it('uses sessionId from opts when response lacks it', async () => {
    const json = JSON.stringify({ text: 'response without sessionId' });
    setupExecFile(json);

    const result = await queryAgent({
      agentId: 'personal',
      sessionId: 'asst-fallback',
      message: 'hi',
    });

    expect(result.sessionId).toBe('asst-fallback');
  });
});
