import { isCursor, isRecord, isSessionId } from "../lib/identifiers.ts";

export type Snapshot = {
  type: "session.snapshot";
  session_id: string;
  event_id: string;
  connection: "disconnected";
  conversation: "off";
  active_turn: null;
  messages: [];
  tasks: [];
  deliveries: [];
};

export type ControlError = {
  type: "error";
  session_id: string;
  event_id: string;
  component: string;
  code: string;
  message: string;
};

export type ServerEvent = Snapshot | ControlError;

export function parseServerEvent(
  value: unknown,
  expectedSessionId: string,
): ServerEvent | null {
  if (typeof value !== "string") {
    return null;
  }
  try {
    const payload: unknown = JSON.parse(value);
    if (
      !isRecord(payload) ||
      payload.session_id !== expectedSessionId ||
      !isCursor(payload.event_id)
    ) {
      return null;
    }
    if (isSnapshot(payload) || isControlError(payload)) {
      return payload;
    }
  } catch {
    return null;
  }
  return null;
}

export function isSessionBootstrap(
  value: unknown,
): value is { session_id: string; last_event_id: null } {
  return (
    isRecord(value) &&
    isSessionId(value.session_id) &&
    value.last_event_id === null
  );
}

export function serializeResumeCommand(
  sessionId: string,
  lastEventId: string | null,
): string {
  return JSON.stringify({
    type: "session.resume",
    session_id: sessionId,
    event_id: crypto.randomUUID(),
    last_event_id: lastEventId,
  });
}

export function serializeStartCommand(sessionId: string): string {
  return JSON.stringify({
    type: "conversation.start",
    session_id: sessionId,
    event_id: crypto.randomUUID(),
  });
}

function isSnapshot(value: Record<string, unknown>): value is Snapshot {
  return (
    value.type === "session.snapshot" &&
    value.connection === "disconnected" &&
    value.conversation === "off" &&
    value.active_turn === null &&
    Array.isArray(value.messages) &&
    value.messages.length === 0 &&
    Array.isArray(value.tasks) &&
    value.tasks.length === 0 &&
    Array.isArray(value.deliveries) &&
    value.deliveries.length === 0
  );
}

function isControlError(value: Record<string, unknown>): value is ControlError {
  return (
    value.type === "error" &&
    typeof value.component === "string" &&
    typeof value.code === "string" &&
    typeof value.message === "string"
  );
}
