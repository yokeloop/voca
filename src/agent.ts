import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import type { AgentResponse } from './types.js';

const execFileAsync = promisify(execFile);

const OPENCLAW_BIN = '/home/priney/.npm-global/bin/openclaw';

export class AgentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AgentError';
  }
}

export interface QueryAgentOpts {
  agentId: string;
  sessionId: string;
  message: string;
  timeoutS?: number;
}

export async function queryAgent(opts: QueryAgentOpts): Promise<AgentResponse> {
  const timeoutS = opts.timeoutS ?? 900;

  let stdout: string;
  try {
    const result = await execFileAsync(
      OPENCLAW_BIN,
      [
        'agent',
        '--agent', opts.agentId,
        '--session-id', opts.sessionId,
        '--message', opts.message,
        '--json',
        '--timeout', String(timeoutS),
      ],
      {
        maxBuffer: 10 * 1024 * 1024,
        timeout: timeoutS * 1000 + 5000,
      },
    );
    stdout = result.stdout;
  } catch (err) {
    throw new AgentError(String((err as { stderr?: unknown }).stderr ?? err));
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    throw new AgentError('invalid response: ' + stdout.slice(0, 200));
  }

  const obj = parsed as Record<string, unknown>;
  return {
    text: String(obj.text ?? ''),
    sessionId: String(obj.sessionId ?? opts.sessionId),
  };
}
