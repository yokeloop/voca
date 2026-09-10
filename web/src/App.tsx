import { useEffect, useRef, useState } from "react";
import {
  connectControl,
  createSession,
  sendStartCheck,
  type ControlError,
  type Snapshot,
} from "./control";

const storageKey = "voca.session";
const storedSessionId =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const storedCursor = /^e[1-9]\d*$/;

type SessionState = { sessionId: string; lastEventId: string | null };
type LoadedSession = { session: SessionState | null; error: string | null };

export function App() {
  const socketRef = useRef<WebSocket | null>(null);
  const cursorRef = useRef<string | null>(null);
  const connectRef = useRef<
    (sessionId: string, lastEventId: string | null) => void
  >(() => undefined);
  const [session, setSession] = useState<SessionState | null>(null);
  const [sessionInput, setSessionInput] = useState("");
  const [cursorInput, setCursorInput] = useState("");
  const [transport, setTransport] = useState("closed");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const restored = loadSession();
    if (restored.error !== null) {
      setError(restored.error);
    }
    if (restored.session === null) {
      return;
    }
    cursorRef.current = restored.session.lastEventId;
    setSession(restored.session);
    setSessionInput(restored.session.sessionId);
    setCursorInput(restored.session.lastEventId ?? "");
    connectRef.current(
      restored.session.sessionId,
      restored.session.lastEventId,
    );
    return () => socketRef.current?.close();
  }, []);

  function persist(next: SessionState): void {
    try {
      localStorage.setItem(
        storageKey,
        JSON.stringify({
          session_id: next.sessionId,
          last_event_id: next.lastEventId,
        }),
      );
    } catch {
      setError("Session persistence between reloads is unavailable.");
    }
  }

  function applyEvent(
    event: Snapshot | ControlError,
    expectedSessionId: string,
  ): void {
    if (event.session_id !== expectedSessionId) {
      return;
    }
    const previous = cursorRef.current;
    if (
      previous !== null &&
      previous !== undefined &&
      cursorValue(event.event_id) <= cursorValue(previous)
    ) {
      return;
    }
    const next = { sessionId: expectedSessionId, lastEventId: event.event_id };
    cursorRef.current = event.event_id;
    setSession(next);
    setSessionInput(expectedSessionId);
    setCursorInput(event.event_id);
    persist(next);
    if (event.type === "session.snapshot") {
      setSnapshot(event);
      setError(null);
    } else {
      setError(`${event.component}/${event.code}: ${event.message}`);
    }
  }

  function connect(sessionId: string, lastEventId: string | null): void {
    socketRef.current?.close();
    setTransport("connecting");
    const socket = connectControl(sessionId, lastEventId, {
      onOpen: () => {
        if (socketRef.current === socket) {
          setTransport("open");
        }
      },
      onClosed: (reason) => {
        if (socketRef.current === socket) {
          setTransport("closed");
          setError(reason);
        }
      },
      onEvent: (event) => {
        if (socketRef.current === socket) {
          applyEvent(event, sessionId);
        }
      },
      onError: (message) => {
        if (socketRef.current === socket) {
          setError(message);
        }
      },
    });
    socketRef.current = socket;
  }

  async function handleCreate(): Promise<void> {
    setCreating(true);
    setError(null);
    try {
      const created = await createSession();
      const next = {
        sessionId: created.sessionId,
        lastEventId: created.lastEventId,
      };
      cursorRef.current = null;
      setSession(next);
      setSessionInput(next.sessionId);
      setCursorInput("");
      setSnapshot(null);
      persist(next);
      connect(next.sessionId, null);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Session creation failed.",
      );
    } finally {
      setCreating(false);
    }
  }

  function restore(): void {
    const cursor = cursorInput.trim() || null;
    const next = { sessionId: sessionInput.trim(), lastEventId: cursor };
    if (next.sessionId.length === 0) {
      setError("Enter a session ID before restoring.");
      return;
    }
    cursorRef.current = next.lastEventId;
    setSession(next);
    setSnapshot(null);
    setError(null);
    persist(next);
    connect(next.sessionId, next.lastEventId);
  }

  function checkStart(): void {
    if (session === null || socketRef.current?.readyState !== WebSocket.OPEN) {
      setError("Open control before checking start.");
      return;
    }
    sendStartCheck(socketRef.current, session.sessionId);
  }

  connectRef.current = connect;

  return (
    <main>
      <h1>VOCA diagnostic session</h1>
      <p>Voice is not implemented yet.</p>
      <section>
        <button
          type="button"
          disabled={creating}
          onClick={() => void handleCreate()}
        >
          {creating ? "Creating session…" : "Create session"}
        </button>
        <button
          type="button"
          disabled={session === null}
          onClick={() =>
            session && connect(session.sessionId, session.lastEventId)
          }
        >
          Reconnect
        </button>
        <button type="button" disabled={session === null} onClick={checkStart}>
          Check start
        </button>
      </section>
      <section>
        <label>
          Session ID
          <input
            value={sessionInput}
            onChange={(event) => setSessionInput(event.target.value)}
          />
        </label>
        <label>
          Last cursor
          <input
            value={cursorInput}
            onChange={(event) => setCursorInput(event.target.value)}
            placeholder="empty for null"
          />
        </label>
        <button type="button" onClick={restore}>
          Restore
        </button>
      </section>
      <dl>
        <dt>Control transport</dt>
        <dd>{transport}</dd>
        <dt>Voice connection</dt>
        <dd>disconnected</dd>
        <dt>Conversation</dt>
        <dd>off</dd>
        <dt>Session ID</dt>
        <dd>{session?.sessionId ?? "none"}</dd>
        <dt>Cursor</dt>
        <dd>{session?.lastEventId ?? "null"}</dd>
      </dl>
      {error !== null && <p role="alert">{error}</p>}
      <section>
        <h2>Last snapshot</h2>
        <pre>
          {snapshot === null
            ? "No snapshot received."
            : JSON.stringify(snapshot, null, 2)}
        </pre>
      </section>
    </main>
  );
}

function loadSession(): LoadedSession {
  try {
    const raw = localStorage.getItem(storageKey);
    if (raw === null) {
      return { session: null, error: null };
    }
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || !isStoredSession(parsed)) {
      localStorage.removeItem(storageKey);
      return {
        session: null,
        error: "Stored session was invalid and was cleared.",
      };
    }
    return {
      session: {
        sessionId: parsed.session_id,
        lastEventId: parsed.last_event_id,
      },
      error: null,
    };
  } catch {
    return {
      session: null,
      error: "Session persistence between reloads is unavailable.",
    };
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isStoredSession(
  value: Record<string, unknown>,
): value is { session_id: string; last_event_id: string | null } {
  return (
    typeof value.session_id === "string" &&
    storedSessionId.test(value.session_id) &&
    (value.last_event_id === null ||
      (typeof value.last_event_id === "string" &&
        storedCursor.test(value.last_event_id)))
  );
}

function cursorValue(value: string): number {
  return Number.parseInt(value.slice(1), 10);
}
