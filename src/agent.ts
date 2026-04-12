import { spawn } from 'node:child_process';
import type { AgentResponse } from './types.js';

export const OPENCLAW_BIN = process.env.OPENCLAW_BIN ?? 'openclaw';

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

function extractText(obj: Record<string, unknown>): string {
  // Format: { result: { payloads: [{ text: "..." }] } }
  const result = obj.result as Record<string, unknown> | undefined;
  if (result) {
    const payloads = result.payloads as Array<Record<string, unknown>> | undefined;
    if (Array.isArray(payloads) && payloads.length > 0) {
      const texts = payloads.map((p) => String(p.text ?? '')).filter(Boolean);
      if (texts.length > 0) return texts.join('\n');
    }
  }
  // Fallback: { text: "..." }
  if (typeof obj.text === 'string' && obj.text) return obj.text;
  return '';
}

export async function queryAgent(opts: QueryAgentOpts): Promise<AgentResponse> {
  const timeoutS = opts.timeoutS ?? 900;
  const args = [
    'agent',
    '--agent', opts.agentId,
    '--session-id', opts.sessionId,
    '--message', opts.message,
    '--json',
    '--timeout', String(timeoutS),
  ];

  console.log(`[agent] calling: ${OPENCLAW_BIN} ${args.join(' ')}`);
  const startTime = Date.now();

  const timer = setInterval(() => {
    const elapsed = Math.round((Date.now() - startTime) / 1000);
    console.log(`[agent] waiting for openclaw... ${elapsed}s`);
  }, 10_000);

  return new Promise<AgentResponse>((resolve, reject) => {
    const child = spawn(OPENCLAW_BIN, args, {
      stdio: ['pipe', 'pipe', 'pipe'],
      timeout: timeoutS * 1000 + 5000,
    });

    const stdoutChunks: Buffer[] = [];

    child.stderr.on('data', (chunk: Buffer) => {
      const line = chunk.toString().trim();
      if (line) console.log(`[agent] ${line}`);
    });

    child.stdout.on('data', (chunk: Buffer) => {
      stdoutChunks.push(chunk);
    });

    child.on('error', (err) => {
      clearInterval(timer);
      const elapsed = Math.round((Date.now() - startTime) / 1000);
      console.error(`[agent] process error after ${elapsed}s:`, err.message);
      reject(new AgentError(err.message));
    });

    child.on('close', (code) => {
      clearInterval(timer);
      const elapsed = Math.round((Date.now() - startTime) / 1000);
      const stdout = Buffer.concat(stdoutChunks).toString();

      if (code !== 0) {
        console.error(`[agent] exited with code ${code} after ${elapsed}s`);
        if (stdout) console.error(`[agent] stdout: ${stdout.slice(0, 500)}`);
        reject(new AgentError(`openclaw exited with code ${code}`));
        return;
      }

      console.log(`[agent] responded in ${elapsed}s`);

      let parsed: unknown;
      try {
        parsed = JSON.parse(stdout);
      } catch {
        reject(new AgentError('invalid response: ' + stdout.slice(0, 200)));
        return;
      }

      const obj = parsed as Record<string, unknown>;
      const text = extractText(obj);
      if (!text) {
        reject(new AgentError('empty response from agent'));
        return;
      }
      resolve({
        text,
        sessionId: String(obj.sessionId ?? opts.sessionId),
      });
    });
  });
}
