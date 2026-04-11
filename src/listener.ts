import { spawn, type ChildProcess } from 'node:child_process';
import { createInterface } from 'node:readline';
import { EventEmitter } from 'node:events';
import { join } from 'node:path';
import type { ListenerHandle } from './types.js';

export interface ListenerOptions {
  pythonBin?: string;
  modelDir: string;
  stub?: boolean;
}

export function spawnListener(opts: ListenerOptions): ListenerHandle {
  const pythonBin = opts.pythonBin ?? 'python3';
  const scriptPath = join(import.meta.dirname, '..', 'listener.py');

  const args = [scriptPath];
  if (opts.stub) {
    args.push('--stub');
  } else {
    args.push('--model-dir', opts.modelDir);
  }

  const child: ChildProcess = spawn(pythonBin, args, {
    stdio: ['pipe', 'pipe', 'inherit'],
  });

  const emitter = new EventEmitter();

  const rl = createInterface({ input: child.stdout! });

  rl.on('line', (line: string) => {
    try {
      const data = JSON.parse(line);
      if (data.event === 'wake') {
        emitter.emit('wake');
      } else if (data.event === 'stop') {
        emitter.emit('stop');
      }
    } catch {
      // Ignore non-JSON lines
    }
  });

  child.on('error', (err) => {
    emitter.emit('error', err);
  });

  child.on('exit', (code) => {
    rl.close();
    emitter.emit('exit', code);
  });

  const handle: ListenerHandle = {
    on(event: 'wake' | 'stop', cb: () => void): void {
      emitter.on(event, cb);
    },

    pause(): void {
      if (child.pid) {
        process.kill(child.pid, 'SIGSTOP');
      }
    },

    resume(): void {
      if (child.pid) {
        process.kill(child.pid, 'SIGCONT');
      }
    },

    kill(): void {
      child.kill('SIGTERM');
    },
  };

  return handle;
}
