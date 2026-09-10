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

type ControlCallbacks = {
  onOpen: () => void;
  onClosed: (reason: string) => void;
  onEvent: (event: ServerEvent) => void;
  onError: (message: string) => void;
};

const eventCursor = /^e[1-9]\d*$/;
const sessionId =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export async function createSession(): Promise<{
  sessionId: string;
  lastEventId: null;
}> {
  const response = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok || !isSessionBootstrap(payload)) {
    throw new Error("Session creation failed; its outcome may be unknown.");
  }
  return { sessionId: payload.session_id, lastEventId: null };
}

export function connectControl(
  sessionId: string,
  lastEventId: string | null,
  callbacks: ControlCallbacks,
): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(
    `${protocol}//${window.location.host}/api/sessions/${sessionId}/control`,
  );
  socket.addEventListener("open", () => {
    callbacks.onOpen();
    socket.send(
      JSON.stringify({
        type: "session.resume",
        session_id: sessionId,
        event_id: crypto.randomUUID(),
        last_event_id: lastEventId,
      }),
    );
  });
  socket.addEventListener("message", (message) => {
    const event = parseServerEvent(message.data, sessionId);
    if (event === null) {
      callbacks.onError("Control sent an invalid event.");
      return;
    }
    callbacks.onEvent(event);
  });
  socket.addEventListener("close", (event) =>
    callbacks.onClosed(event.reason || `Control closed (${event.code}).`),
  );
  socket.addEventListener("error", () =>
    callbacks.onError("Control transport failed."),
  );
  return socket;
}

export function sendStartCheck(socket: WebSocket, sessionId: string): void {
  socket.send(
    JSON.stringify({
      type: "conversation.start",
      session_id: sessionId,
      event_id: crypto.randomUUID(),
    }),
  );
}

function parseServerEvent(
  value: unknown,
  expectedSessionId: string,
): ServerEvent | null {
  if (typeof value !== "string") {
    return null;
  }
  try {
    const payload: unknown = JSON.parse(value);
    if (
      !isObject(payload) ||
      payload.session_id !== expectedSessionId ||
      !isCursor(payload.event_id)
    ) {
      return null;
    }
    if (isSnapshot(payload)) {
      return payload;
    }
    if (isControlError(payload)) {
      return payload;
    }
  } catch {
    return null;
  }
  return null;
}

function isSessionBootstrap(
  value: unknown,
): value is { session_id: string; last_event_id: null } {
  return (
    isObject(value) &&
    isSessionId(value.session_id) &&
    value.last_event_id === null
  );
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

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isSessionId(value: unknown): value is string {
  return typeof value === "string" && sessionId.test(value);
}

function isCursor(value: unknown): value is string {
  return typeof value === "string" && eventCursor.test(value);
}
