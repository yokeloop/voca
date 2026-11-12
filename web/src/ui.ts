import { ConnectionState } from "livekit-client";
import type { Transcript } from "./room";

const AGENT_LABELS: Record<string, string> = {
  initializing: "агент запускается",
  listening: "слушает",
  thinking: "думает",
  speaking: "говорит",
};

let button: HTMLButtonElement;
let status: HTMLParagraphElement;
let transcript: HTMLOListElement;
const rows = new Map<string, HTMLLIElement>();

export function render(app: HTMLElement): HTMLButtonElement {
  const title = document.createElement("h1");
  title.textContent = "VOCA";
  button = document.createElement("button");
  button.type = "button";
  status = document.createElement("p");
  transcript = document.createElement("ol");
  app.replaceChildren(title, button, status, transcript);
  setConnected(false);
  return button;
}

export function setConnected(connected: boolean): void {
  button.textContent = connected ? "Отключиться" : "Подключиться";
}

export function setState(text: string): void {
  status.textContent = text;
}

export function stateLabel(
  connection: ConnectionState,
  agent: string | undefined,
): string {
  if (connection === ConnectionState.Disconnected) {
    return "отключено";
  }
  if (connection !== ConnectionState.Connected) {
    return "подключение…";
  }
  return (agent !== undefined && AGENT_LABELS[agent]) || "ждём агента";
}

export function upsertSegment({ segment, who, text, final }: Transcript): void {
  let row = rows.get(segment);
  if (row === undefined) {
    row = document.createElement("li");
    rows.set(segment, row);
    transcript.append(row);
  }
  row.textContent = `${who}: ${text}`;
  row.style.opacity = final ? "1" : "0.6";
}
