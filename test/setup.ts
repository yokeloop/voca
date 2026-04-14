import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterAll } from 'vitest';

let ownedDir: string | null = null;

if (!process.env.VOCA_HOME) {
  ownedDir = fs.mkdtempSync(path.join(os.tmpdir(), 'voca-test-home-'));
  process.env.VOCA_HOME = ownedDir;
}

afterAll(() => {
  if (ownedDir) fs.rmSync(ownedDir, { recursive: true, force: true });
});
