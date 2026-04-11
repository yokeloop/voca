import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import type { VocaSession } from './types.js';

export const SESSION_PATH = path.join(os.homedir(), '.openclaw/assistant/session.json');

export function generateSessionId(): string {
  return 'asst-' + Date.now();
}

export function newSession(profile: string): VocaSession {
  return {
    sessionId: generateSessionId(),
    messageCount: 0,
    profile,
    createdAt: new Date().toISOString(),
  };
}

async function ensureSessionDir(sessionPath: string): Promise<void> {
  await fs.mkdir(path.dirname(sessionPath), { recursive: true });
}

export async function readSession(sessionPath: string = SESSION_PATH): Promise<VocaSession> {
  try {
    const raw = await fs.readFile(sessionPath, 'utf-8');
    return JSON.parse(raw) as VocaSession;
  } catch (err: unknown) {
    if (err instanceof Error && 'code' in err && (err as NodeJS.ErrnoException).code === 'ENOENT') {
      return newSession('personal');
    }
    throw err;
  }
}

export async function writeSession(s: VocaSession, sessionPath: string = SESSION_PATH): Promise<void> {
  await ensureSessionDir(sessionPath);
  await fs.writeFile(sessionPath, JSON.stringify(s, null, 2) + '\n', 'utf-8');
}

export async function incrementMessageCount(sessionPath: string = SESSION_PATH): Promise<VocaSession> {
  const session = await readSession(sessionPath);
  session.messageCount += 1;
  await writeSession(session, sessionPath);
  return session;
}

export async function resetSessionForProfile(profile: string, sessionPath: string = SESSION_PATH): Promise<VocaSession> {
  const session = newSession(profile);
  await writeSession(session, sessionPath);
  return session;
}
