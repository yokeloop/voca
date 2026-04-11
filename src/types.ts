export interface VocaConfig {
  inputDevice: string;
  outputDevice: string;
  profile: string;
  wakeWord: string;
  stopWord: string;
  piperModel: string;
  piperBin: string;
  language: string;
}

export interface VocaSession {
  sessionId: string;
  messageCount: number;
  profile: string;
  createdAt: string;
}

export type DaemonState =
  | 'IDLE'
  | 'LISTENING'
  | 'RECORDING'
  | 'PROCESSING'
  | 'SPEAKING';

export type DaemonEvent =
  | 'WAKE'
  | 'START_RECORD'
  | 'STOP'
  | 'RECORD_CANCEL'
  | 'PROCESSING_DONE'
  | 'SPEAKING_DONE'
  | 'ERROR';

export interface ListenerHandle {
  on(event: 'wake' | 'stop', cb: () => void): void;
  pause(): void;
  resume(): void;
  kill(): void;
}

export interface RecorderHandle {
  filePath: string;
  stop(): void;
  cancel(): void;
  on(event: 'done' | 'cancel', cb: () => void): void;
}

export interface AgentResponse {
  text: string;
  sessionId: string;
}
