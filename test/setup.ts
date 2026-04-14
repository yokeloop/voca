import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

if (!process.env.VOCA_HOME) {
  process.env.VOCA_HOME = fs.mkdtempSync(path.join(os.tmpdir(), 'voca-test-home-'));
}
