import { useEffect, useRef, useState } from "react";

import { createControlConnection } from "../api/control-connection";
import { createSession } from "../api/http";
import { isCursor, isSessionId } from "../lib/identifiers";
import {
  applyServerEvent,
  resetProjection,
  type SessionProjection,
} from "./projection";
import {
  loadSession,
  persistSession,
  type StoredSession,
} from "./session-storage";

export function useSessionConsole() {
  const [session, setSession] = useState<StoredSession | null>(null);
  const [projection, setProjection] = useState<SessionProjection | null>(null);
  const [sessionInput, setSessionInput] = useState("");
  const [cursorInput, setCursorInput] = useState("");
  const [transport, setTransport] = useState("closed");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const connection = useRef<ReturnType<typeof createControlConnection> | null>(
    null,
  );
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    connection.current = createControlConnection({
      onOpen: () => setTransport("open"),
      onClosed: (reason) => {
        setTransport("closed");
        setError(reason);
      },
      onError: setError,
      onEvent: (event) => {
        setProjection((previous) => {
          if (previous === null) return previous;
          const next = applyServerEvent(previous, event);
          if (next !== previous) {
            const persistenceError = persistSession({
              sessionId: next.sessionId,
              lastEventId: next.lastEventId,
            });
            if (persistenceError !== null) setError(persistenceError);
            setSession({
              sessionId: next.sessionId,
              lastEventId: next.lastEventId,
            });
            setSessionInput(next.sessionId);
            setCursorInput(next.lastEventId ?? "");
          }
          return next;
        });
      },
    });
    const restored = loadSession();
    if (restored.error !== null) setError(restored.error);
    if (restored.session !== null) {
      setSession(restored.session);
      setSessionInput(restored.session.sessionId);
      setCursorInput(restored.session.lastEventId ?? "");
      setProjection(resetProjection(restored.session.sessionId));
      setTransport("connecting");
      connection.current.connect(
        restored.session.sessionId,
        restored.session.lastEventId,
      );
    }
    return () => {
      mounted.current = false;
      connection.current?.close();
    };
  }, []);

  async function handleCreate(): Promise<void> {
    setCreating(true);
    setError(null);
    try {
      const created = await createSession();
      if (!mounted.current) return;
      const next = { sessionId: created.sessionId, lastEventId: null };
      setSession(next);
      setProjection(resetProjection(next.sessionId));
      setSessionInput(next.sessionId);
      setCursorInput("");
      const persistenceError = persistSession(next);
      if (persistenceError !== null) setError(persistenceError);
      setTransport("connecting");
      connection.current?.connect(next.sessionId, null);
    } catch (reason) {
      if (mounted.current)
        setError(
          reason instanceof Error ? reason.message : "Session creation failed.",
        );
    } finally {
      if (mounted.current) setCreating(false);
    }
  }

  function reconnect(): void {
    if (projection === null) return;
    setTransport("connecting");
    connection.current?.connect(projection.sessionId, projection.lastEventId);
  }

  function restore(): void {
    const sessionId = sessionInput.trim();
    const cursor = cursorInput.trim() || null;
    if (!isSessionId(sessionId)) {
      setError("Enter a canonical session ID before restoring.");
      return;
    }
    if (cursor !== null && !isCursor(cursor)) {
      setError("Enter a canonical cursor before restoring.");
      return;
    }
    setSession({ sessionId, lastEventId: null });
    setProjection(resetProjection(sessionId));
    setError(null);
    setTransport("connecting");
    connection.current?.connect(sessionId, cursor);
  }

  function checkStart(): void {
    if (!connection.current?.checkStart())
      setError("Open control before checking start.");
  }

  return {
    session,
    projection,
    sessionInput,
    cursorInput,
    transport,
    error,
    creating,
    setSessionInput,
    setCursorInput,
    handleCreate,
    reconnect,
    restore,
    checkStart,
  };
}
