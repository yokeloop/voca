import type { DaemonState, DaemonEvent } from './types.js';

export class InvalidTransitionError extends Error {
  constructor(
    public readonly state: DaemonState,
    public readonly event: DaemonEvent,
  ) {
    super(`Invalid transition: ${state} + ${event}`);
    this.name = 'InvalidTransitionError';
  }
}

const transitionTable: Record<string, DaemonState> = {
  'IDLE+WAKE': 'LISTENING',
  'LISTENING+START_RECORD': 'RECORDING',
  'LISTENING+STOP': 'IDLE',
  'RECORDING+STOP': 'PROCESSING',
  'RECORDING+RECORD_CANCEL': 'IDLE',
  'RECORDING+ERROR': 'IDLE',
  'PROCESSING+PROCESSING_DONE': 'SPEAKING',
  'PROCESSING+ERROR': 'IDLE',
  'SPEAKING+SPEAKING_DONE': 'IDLE',
  'SPEAKING+ERROR': 'IDLE',
  'SPEAKING+WAKE_INTERRUPT': 'RECORDING',
};

export function transition(state: DaemonState, event: DaemonEvent): DaemonState {
  const key = `${state}+${event}`;
  const next = transitionTable[key];
  if (next === undefined) {
    throw new InvalidTransitionError(state, event);
  }
  return next;
}
