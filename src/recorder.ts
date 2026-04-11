import { spawn, type ChildProcess } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { unlink } from 'node:fs/promises';
import type { RecorderHandle } from './types.js';

const MAX_DURATION_MS = 120_000; // 2 minutes

export function startRecording(opts: {
  device: string;
  tmpDir?: string;
}): RecorderHandle {
  const filePath = join(
    opts.tmpDir || tmpdir(),
    'voca-rec-' + Date.now() + '.wav',
  );

  const emitter = new EventEmitter();
  let cancelled = false;
  let stopped = false;

  const child: ChildProcess = spawn('sox', [
    '-t', 'alsa', opts.device,
    filePath,
    'silence', '1', '0.1', '0.1%', '1', '30', '0.1%',
  ]);

  const timer = setTimeout(() => {
    handle.cancel();
  }, MAX_DURATION_MS);

  child.on('exit', (code) => {
    clearTimeout(timer);
    if (cancelled) return;
    if (code === 0) {
      emitter.emit('done');
    } else {
      emitter.emit('cancel');
    }
  });

  child.on('error', (err) => {
    clearTimeout(timer);
    if (!cancelled) {
      cancelled = true;
      emitter.emit('cancel');
      unlink(filePath).catch(() => {});
    }
  });

  const handle: RecorderHandle = {
    filePath,

    stop() {
      if (stopped) return;
      stopped = true;
      clearTimeout(timer);
      child.kill('SIGTERM');
    },

    cancel() {
      if (cancelled) return;
      cancelled = true;
      stopped = true;
      clearTimeout(timer);
      child.kill('SIGTERM');
      emitter.emit('cancel');
      unlink(filePath).catch(() => {});
    },

    on(event: 'done' | 'cancel', cb: () => void) {
      emitter.on(event, cb);
    },
  };

  return handle;
}
