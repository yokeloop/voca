import { describe, it, expect } from 'vitest';
import { transition, InvalidTransitionError } from '../src/daemon-state.js';
import type { DaemonState, DaemonEvent } from '../src/types.js';

describe('transition', () => {
  const validCases: [DaemonState, DaemonEvent, DaemonState][] = [
    ['IDLE', 'WAKE', 'LISTENING'],
    ['LISTENING', 'START_RECORD', 'RECORDING'],
    ['LISTENING', 'STOP', 'IDLE'],
    ['RECORDING', 'STOP', 'PROCESSING'],
    ['RECORDING', 'RECORD_CANCEL', 'IDLE'],
    ['RECORDING', 'ERROR', 'IDLE'],
    ['PROCESSING', 'PROCESSING_DONE', 'SPEAKING'],
    ['PROCESSING', 'ERROR', 'IDLE'],
    ['SPEAKING', 'SPEAKING_DONE', 'IDLE'],
    ['SPEAKING', 'ERROR', 'IDLE'],
  ];

  it.each(validCases)(
    '%s + %s → %s',
    (state, event, expected) => {
      expect(transition(state, event)).toBe(expected);
    },
  );

  const invalidCases: [DaemonState, DaemonEvent][] = [
    ['IDLE', 'STOP'],
    ['IDLE', 'START_RECORD'],
    ['IDLE', 'RECORD_CANCEL'],
    ['IDLE', 'PROCESSING_DONE'],
    ['IDLE', 'SPEAKING_DONE'],
    ['IDLE', 'ERROR'],
    ['LISTENING', 'WAKE'],
    ['LISTENING', 'RECORD_CANCEL'],
    ['LISTENING', 'PROCESSING_DONE'],
    ['LISTENING', 'SPEAKING_DONE'],
    ['LISTENING', 'ERROR'],
    ['RECORDING', 'WAKE'],
    ['RECORDING', 'START_RECORD'],
    ['RECORDING', 'PROCESSING_DONE'],
    ['RECORDING', 'SPEAKING_DONE'],
    ['PROCESSING', 'WAKE'],
    ['PROCESSING', 'START_RECORD'],
    ['PROCESSING', 'STOP'],
    ['PROCESSING', 'RECORD_CANCEL'],
    ['PROCESSING', 'SPEAKING_DONE'],
    ['SPEAKING', 'WAKE'],
    ['SPEAKING', 'START_RECORD'],
    ['SPEAKING', 'STOP'],
    ['SPEAKING', 'RECORD_CANCEL'],
    ['SPEAKING', 'PROCESSING_DONE'],
  ];

  it.each(invalidCases)(
    '%s + %s → throws InvalidTransitionError',
    (state, event) => {
      expect(() => transition(state, event)).toThrow(InvalidTransitionError);
    },
  );

  it('InvalidTransitionError exposes state and event', () => {
    try {
      transition('IDLE', 'STOP');
      expect.fail('should have thrown');
    } catch (e) {
      expect(e).toBeInstanceOf(InvalidTransitionError);
      const err = e as InvalidTransitionError;
      expect(err.state).toBe('IDLE');
      expect(err.event).toBe('STOP');
      expect(err.message).toBe('Invalid transition: IDLE + STOP');
    }
  });
});
