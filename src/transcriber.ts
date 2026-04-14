import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);

export class TranscribeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TranscribeError';
  }
}

export async function transcribe(
  filePath: string,
  opts: { language?: string } = {},
): Promise<string> {
  const language = opts.language;
  if (!language) {
    throw new TranscribeError(
      "language is not configured — run 'voca bootstrap' to select a language",
    );
  }

  let stdout: string;
  try {
    const result = await execFileAsync(
      '/usr/local/bin/whisper-stt-wrapper',
      [filePath, '--language', language],
      { maxBuffer: 1024 * 1024 },
    );
    stdout = result.stdout;
  } catch (err) {
    throw new TranscribeError(String((err as { stderr?: unknown }).stderr ?? err));
  }

  let text = stdout.trim();
  text = text.replace(/\s*stop\s*$/i, '').trim();
  return text;
}
