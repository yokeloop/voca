import { cursorValue } from "../lib/identifiers.ts";
import type { ControlError, ServerEvent, Snapshot } from "../api/protocol.ts";

export type SessionProjection = {
  sessionId: string;
  lastEventId: string | null;
  snapshot: Snapshot | null;
  error: ControlError | null;
};

export function resetProjection(sessionId: string): SessionProjection {
  return { sessionId, lastEventId: null, snapshot: null, error: null };
}

export function applyServerEvent(
  state: SessionProjection,
  event: ServerEvent,
): SessionProjection {
  if (
    event.session_id !== state.sessionId ||
    (state.lastEventId !== null &&
      cursorValue(event.event_id) <= cursorValue(state.lastEventId))
  )
    return state;
  if (event.type === "session.snapshot")
    return {
      ...state,
      lastEventId: event.event_id,
      snapshot: event,
      error: null,
    };
  return { ...state, lastEventId: event.event_id, error: event };
}
