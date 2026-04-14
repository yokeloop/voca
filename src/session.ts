import fs from 'node:fs/promises';
import path from 'node:path';
import type { VocaSession } from './types.js';
import { sessionPath } from './paths.js';

export { sessionPath };

let sessionCounter = 0;

export function generateSessionId(): string {
  return 'asst-' + Date.now() + '-' + (sessionCounter++);
}

export function newSession(profile: string): VocaSession {
  return {
    sessionId: generateSessionId(),
    messageCount: 0,
    profile,
    createdAt: new Date().toISOString(),
  };
}

export async function readSession(file: string = sessionPath()): Promise<VocaSession> {
  try {
    const raw = await fs.readFile(file, 'utf-8');
    return JSON.parse(raw) as VocaSession;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return newSession('personal');
    throw err;
  }
}

export async function writeSession(s: VocaSession, file: string = sessionPath()): Promise<void> {
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, JSON.stringify(s, null, 2) + '\n', 'utf-8');
}

export async function incrementMessageCount(file: string = sessionPath()): Promise<VocaSession> {
  const session = await readSession(file);
  session.messageCount += 1;
  await writeSession(session, file);
  return session;
}

export async function resetSessionForProfile(profile: string, file: string = sessionPath()): Promise<VocaSession> {
  const session = newSession(profile);
  await writeSession(session, file);
  return session;
}
