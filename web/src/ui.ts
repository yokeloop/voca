import { ConnectionState } from "livekit-client";
import type {
  ActionResult,
  CardsState,
  HistoryItem,
  TaskCard,
  Transcript,
} from "./room";

export type TaskAction = {
  kind: "approve" | "deny" | "cancel";
  card: TaskCard;
};

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
let tasks: HTMLOListElement;
let transcript: HTMLOListElement;
const rows = new Map<string, HTMLLIElement>();
const taskRows = new Map<string, HTMLLIElement>();
let onTaskAction: ((action: TaskAction) => Promise<ActionResult>) | undefined;

export function setTaskActions(
  handler: (action: TaskAction) => Promise<ActionResult>,
): void {
  onTaskAction = handler;
}

export function render(app: HTMLElement): HTMLButtonElement {
  const title = document.createElement("h1");
  title.textContent = "VOCA";
  button = document.createElement("button");
  button.type = "button";
  status = document.createElement("p");
  components = document.createElement("p");
  errors = document.createElement("p");
  tasks = document.createElement("ol");
  transcript = document.createElement("ol");
  app.replaceChildren(
    title,
    button,
    status,
    components,
    errors,
    tasks,
    transcript,
  );
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

export function setErrorLine(text: string): void {
  errors.textContent = text;
}

export function replaceHistory(items: HistoryItem[]): void {
  rows.clear();
  transcript.replaceChildren(
    ...items.map(({ who, text }) => {
      const row = document.createElement("li");
      row.textContent =
        who === "сжатие" ? "— контекст сжат —" : `${who}: ${text}`;
      row.style.opacity = "1";
      return row;
    }),
  );
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

const TASK_LABELS: Record<TaskCard["status"], string> = {
  queued: "в очереди",
  running: "выполняется",
  waiting_approval: "ждёт подтверждения",
  unknown: "статус неизвестен",
  succeeded: "готово",
  failed: "не выполнено",
};

const OPEN = new Set<TaskCard["status"]>([
  "queued",
  "running",
  "waiting_approval",
  "unknown",
]);

function paragraph(text: string): HTMLParagraphElement {
  const element = document.createElement("p");
  element.textContent = text;
  return element;
}

function preformatted(text: string): HTMLPreElement {
  const element = document.createElement("pre");
  element.textContent = text;
  element.style.whiteSpace = "pre-wrap";
  element.style.overflowWrap = "anywhere";
  return element;
}

function fillTask(row: HTMLLIElement, card: TaskCard): void {
  const parts: HTMLElement[] = [
    paragraph(
      card.delivered_at !== null
        ? `${TASK_LABELS[card.status]} · озвучено`
        : TASK_LABELS[card.status],
    ),
    paragraph(card.text),
  ];
  if (card.status === "waiting_approval" && card.approval !== null) {
    parts.push(
      paragraph(`Hermes просит: ${card.approval.description ?? ""}`),
      preformatted(card.approval.command ?? ""),
      paragraph(card.approval.hint),
    );
  }
  const buttons: HTMLButtonElement[] = [];
  const action = (kind: TaskAction["kind"], label: string) => {
    const element = document.createElement("button");
    element.type = "button";
    element.textContent = label;
    element.addEventListener("click", async () => {
      for (const item of buttons) {
        item.disabled = true;
      }
      const result =
        onTaskAction === undefined
          ? { ok: false, error: "нет подключения" }
          : await onTaskAction({ kind, card });
      if (!result.ok) {
        for (const item of buttons) {
          item.disabled = false;
        }
        setErrorLine(`ошибка: ${result.error}`);
      }
    });
    buttons.push(element);
  };
  if (
    card.status === "waiting_approval" &&
    card.approval !== null &&
    card.approval.request_id !== null
  ) {
    action("approve", "Подтвердить");
    action("deny", "Отклонить");
  }
  if (OPEN.has(card.status)) {
    action("cancel", "Отменить задачу");
  }
  if (card.result !== null) {
    parts.push(preformatted(card.result));
  }
  if (card.error !== null) {
    parts.push(paragraph(`ошибка: ${card.error}`));
  }
  if (buttons.length > 0) {
    const actions = document.createElement("p");
    actions.append(...buttons);
    parts.push(actions);
  }
  row.replaceChildren(...parts);
}

export function replaceTasks(cards: TaskCard[]): void {
  taskRows.clear();
  tasks.replaceChildren(
    ...cards.map((card) => {
      const row = document.createElement("li");
      fillTask(row, card);
      taskRows.set(card.tool_call_id, row);
      return row;
    }),
  );
}

export function upsertTask(card: TaskCard): void {
  const existing = taskRows.get(card.tool_call_id);
  if (existing !== undefined) {
    fillTask(existing, card);
    return;
  }
  const row = document.createElement("li");
  fillTask(row, card);
  taskRows.set(card.tool_call_id, row);
  tasks.prepend(row);
}
