import { isCursor, isRecord, isSessionId } from "../lib/identifiers";

const storageKey = "voca.session";
export type StoredSession = { sessionId: string; lastEventId: string | null };

export function loadSession(): {
  session: StoredSession | null;
  error: string | null;
} {
  try {
    const raw = localStorage.getItem(storageKey);
    if (raw === null) return { session: null, error: null };
    const value: unknown = JSON.parse(raw);
    if (
      !isRecord(value) ||
      !isSessionId(value.session_id) ||
      (value.last_event_id !== null && !isCursor(value.last_event_id))
    ) {
      localStorage.removeItem(storageKey);
      return {
        session: null,
        error: "Stored session was invalid and was cleared.",
      };
    }
    return {
      session: {
        sessionId: value.session_id,
        lastEventId: value.last_event_id,
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

export function persistSession(session: StoredSession): string | null {
  try {
    localStorage.setItem(
      storageKey,
      JSON.stringify({
        session_id: session.sessionId,
        last_event_id: session.lastEventId,
      }),
    );
    return null;
  } catch {
    return "Session persistence between reloads is unavailable.";
  }
}
