import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {
  generateSessionId,
  newSession,
  readSession,
  writeSession,
  incrementMessageCount,
  resetSessionForProfile,
} from '../src/session.js';

describe('session', () => {
  let tmpDir: string;
  let sessionPath: string;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'voca-session-test-'));
    sessionPath = path.join(tmpDir, 'session.json');
  });

  afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  it('generateSessionId returns asst- prefixed string', () => {
    const id = generateSessionId();
    expect(id).toMatch(/^asst-\d+$/);
  });

  it('newSession creates a fresh session object', () => {
    const s = newSession('personal');
    expect(s.sessionId).toMatch(/^asst-\d+$/);
    expect(s.messageCount).toBe(0);
    expect(s.profile).toBe('personal');
    expect(s.createdAt).toBeTruthy();
  });

  it('readSession returns default session when file does not exist', async () => {
    const s = await readSession(sessionPath);
    expect(s.sessionId).toMatch(/^asst-\d+$/);
    expect(s.profile).toBe('personal');
    expect(s.messageCount).toBe(0);
  });

  it('writeSession and readSession round-trip', async () => {
    const s = newSession('public');
    await writeSession(s, sessionPath);

    const read = await readSession(sessionPath);
    expect(read).toEqual(s);
  });

  it('incrementMessageCount increments and persists', async () => {
    const s = newSession('personal');
    await writeSession(s, sessionPath);

    const s1 = await incrementMessageCount(sessionPath);
    expect(s1.messageCount).toBe(1);

    const s2 = await incrementMessageCount(sessionPath);
    expect(s2.messageCount).toBe(2);

    const read = await readSession(sessionPath);
    expect(read.messageCount).toBe(2);
  });

  it('resetSessionForProfile creates new session with given profile', async () => {
    const s1 = newSession('personal');
    await writeSession(s1, sessionPath);

    const s2 = await resetSessionForProfile('public', sessionPath);
    expect(s2.profile).toBe('public');
    expect(s2.sessionId).not.toBe(s1.sessionId);
    expect(s2.messageCount).toBe(0);

    const read = await readSession(sessionPath);
    expect(read.profile).toBe('public');
  });

  it('profile change resets session ID', async () => {
    const s1 = await resetSessionForProfile('personal', sessionPath);
    const id1 = s1.sessionId;

    // small delay to ensure different timestamp
    await new Promise((r) => setTimeout(r, 5));

    const s2 = await resetSessionForProfile('public', sessionPath);
    expect(s2.sessionId).not.toBe(id1);
    expect(s2.profile).toBe('public');
  });

  it('writeSession creates nested directories', async () => {
    const nestedPath = path.join(tmpDir, 'x', 'y', 'session.json');
    const s = newSession('personal');
    await writeSession(s, nestedPath);

    const read = await readSession(nestedPath);
    expect(read).toEqual(s);
  });
});
