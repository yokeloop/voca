import {
  parseServerEvent,
  serializeResumeCommand,
  serializeStartCommand,
  type ServerEvent,
} from "./protocol";

type Callbacks = {
  onOpen: () => void;
  onClosed: (reason: string) => void;
  onEvent: (event: ServerEvent) => void;
  onError: (message: string) => void;
};

export function createControlConnection(callbacks: Callbacks): {
  connect(sessionId: string, lastEventId: string | null): void;
  close(): void;
  checkStart(): boolean;
} {
  let socket: WebSocket | null = null;
  let owner = 0;

  function connect(sessionId: string, lastEventId: string | null): void {
    close();
    const currentOwner = ++owner;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const next = new WebSocket(
      `${protocol}//${window.location.host}/api/sessions/${sessionId}/control`,
    );
    socket = next;
    next.addEventListener("open", () => {
      if (currentOwner !== owner) return;
      callbacks.onOpen();
      next.send(serializeResumeCommand(sessionId, lastEventId));
    });
    next.addEventListener("message", (message) => {
      if (currentOwner !== owner) return;
      const event = parseServerEvent(message.data, sessionId);
      if (event === null) callbacks.onError("Control sent an invalid event.");
      else callbacks.onEvent(event);
    });
    next.addEventListener("close", (event) => {
      if (currentOwner === owner)
        callbacks.onClosed(event.reason || `Control closed (${event.code}).`);
    });
    next.addEventListener("error", () => {
      if (currentOwner === owner)
        callbacks.onError("Control transport failed.");
    });
  }

  function close(): void {
    owner += 1;
    const previous = socket;
    socket = null;
    previous?.close();
  }

  function checkStart(): boolean {
    if (socket?.readyState !== WebSocket.OPEN) return false;
    socket.send(
      serializeStartCommand(new URL(socket.url).pathname.split("/")[3]),
    );
    return true;
  }

  return { connect, close, checkStart };
}
