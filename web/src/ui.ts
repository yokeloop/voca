import { ConnectionState } from "livekit-client";
import type { CardsState, Transcript } from "./room";

const AGENT_LABELS: Record<string, string> = {
  initializing: "агент запускается",
  listening: "слушает",
  thinking: "думает",
  speaking: "говорит",
};

let button: HTMLButtonElement;
let status: HTMLParagraphElement;
let components: HTMLParagraphElement;
let errors: HTMLParagraphElement;
let transcript: HTMLOListElement;
const rows = new Map<string, HTMLLIElement>();

export function render(app: HTMLElement): HTMLButtonElement {
  const title = document.createElement("h1");
  title.textContent = "VOCA";
  button = document.createElement("button");
  button.type = "button";
  status = document.createElement("p");
  components = document.createElement("p");
  errors = document.createElement("p");
  transcript = document.createElement("ol");
  app.replaceChildren(title, button, status, components, errors, transcript);
  setConnected(false);
  setCards(undefined);
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

const COMPONENT_NAMES = [
  ["stt", "STT"],
  ["llm", "LLM"],
  ["tts", "TTS"],
] as const;

export function cardsLabel(state: CardsState | undefined): string {
  if (state === undefined) {
    return "состояние агента неизвестно";
  }
  const parts = COMPONENT_NAMES.map(
    ([key, name]) => `${name}: ${state.components[key]}`,
  );
  parts.push(
    `инструкции: ${state.instructions_source} (${state.instructions_sha256.slice(0, 8)})`,
  );
  return parts.join(" · ");
}

export function errorLine(state: CardsState | undefined): string {
  if (state === undefined) {
    return "";
  }
  const parts = COMPONENT_NAMES.filter(
    ([key]) => state.components[key] === "down",
  ).map(([, name]) => `${name} недоступен`);
  if (state.instructions_source === "last_valid") {
    parts.push(
      `инструкции: прошлый рабочий набор — ${state.instructions_error}`,
    );
  }
  return parts.join("; ");
}

export function setCards(state: CardsState | undefined): void {
  components.textContent = cardsLabel(state);
  errors.textContent = errorLine(state);
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
