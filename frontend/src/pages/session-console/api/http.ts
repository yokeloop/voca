import { isSessionBootstrap } from "./protocol";

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
