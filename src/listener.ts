import { spawn, type ChildProcess } from 'node:child_process';
import { createInterface } from 'node:readline';
import { EventEmitter } from 'node:events';
import { join } from 'node:path';
import type { ListenerHandle } from './types.js';

export interface ListenerOptions {
  pythonBin?: string;
  modelDir: string;
  stub?: boolean;
  deviceIndex?: number;
}

export function spawnListener(opts: ListenerOptions): ListenerHandle {
  const pythonBin = opts.pythonBin ?? 'python3';
  const scriptPath = join(import.meta.dirname, '..', 'listener.py');

  const args = opts.stub
    ? [scriptPath, '--stub']
    : [scriptPath, '--model-dir', opts.modelDir];

  if (!opts.stub && opts.deviceIndex !== undefined) {
    args.push('--device-index', String(opts.deviceIndex));
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
      } else if (data.event === 'recorded') {
        emitter.emit('recorded', data.path);
      } else if (data.event === 'cancelled') {
        emitter.emit('cancelled');
      }
    } catch {
      // ignore non-JSON lines
    }
  });

  child.on('error', (err) => {
    emitter.emit('error', err);
  });

  child.on('exit', (code) => {
    rl.close();
    emitter.emit('exit', code);
  });

  return {
    on(event: 'wake' | 'stop' | 'recorded' | 'cancelled' | 'exit', cb: (...args: any[]) => void): void {
      emitter.on(event, cb);
    },
    pause(): void {
      if (child.pid) process.kill(child.pid, 'SIGUSR1');
    },
    resume(): void {
      if (child.pid) process.kill(child.pid, 'SIGUSR2');
    },
    kill(): void {
      child.kill('SIGTERM');
    },
  };
}
