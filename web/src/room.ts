import {
  type ConnectionState,
  createLocalAudioTrack,
  type DisconnectReason,
  type Participant,
  Room,
  RoomEvent,
  Track,
} from "livekit-client";
import { ClientTiming } from "./timing";

const DEVICE_RE = /^[a-z0-9-]{1,32}$/;
const DEVICE_KEY = "voca-device";
const AGENT_STATE = "lk.agent.state";
const METRICS_TOPIC = "voca.metrics";
const HISTORY_TOPIC = "voca.history";

export interface Transcript {
  segment: string;
  who: string;
  text: string;
  final: boolean;
}

export interface CardsState {
  instructions_sha256: string;
  instructions_source: "fresh" | "last_valid";
  instructions_error: string | null;
  components: {
    stt: "ok" | "down";
    llm: "ok" | "down";
    tts: "ok" | "down";
  };
}

export interface HistoryItem {
  id: string;
  at: number;
  who: "вы" | "агент" | "сжатие";
  text: string;
}

export type TaskStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "unknown"
  | "succeeded"
  | "failed";

export interface TaskApproval {
  description: string | null;
  command: string | null;
  choices: string[] | null;
  request_id: string | null;
  hint: string;
}

export interface TaskCard {
  tool_call_id: string;
  status: TaskStatus;
  text: string;
  result: string | null;
  error: string | null;
  created_at: number;
  delivered_at: number | null;
  approval: TaskApproval | null;
}

export interface Handlers {
  transcript(transcript: Transcript): void;
  cards(state: CardsState): void;
  history(items: HistoryItem[]): void;
  tasks(cards: TaskCard[]): void;
  task(card: TaskCard): void;
  agentState(state: string | undefined): void;
  connection(state: ConnectionState): void;
  disconnected(reason: DisconnectReason | undefined): void;
}

function randomDevice(): string {
  const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  return `browser-${Array.from(bytes, (byte) => alphabet[byte % alphabet.length]).join("")}`;
}

export function deviceName(): string {
  const fromQuery = new URLSearchParams(location.search).get("device");
  if (fromQuery !== null) {
    if (!DEVICE_RE.test(fromQuery)) {
      throw new Error(`device must match ${DEVICE_RE.source}`);
    }
    return fromQuery;
  }
  const stored = localStorage.getItem(DEVICE_KEY);
  if (stored !== null && DEVICE_RE.test(stored)) {
    return stored;
  }
  const device = randomDevice();
  localStorage.setItem(DEVICE_KEY, device);
  return device;
}

function reportAgent(
  participant: Participant,
  handlers: Handlers,
  onState: (state: string) => void,
): void {
  const state = participant.attributes[AGENT_STATE];
  if (state !== undefined) {
    handlers.agentState(state);
    onState(state);
  }
}

async function requestToken(
  device: string,
): Promise<{ token: string; url: string }> {
  const response = await fetch("/api/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device }),
  });
  if (!response.ok) {
    throw new Error(`/api/token: ${response.status}`);
  }
  return (await response.json()) as { token: string; url: string };
}

export async function connect(
  device: string,
  handlers: Handlers,
): Promise<Room> {
  let timing: ClientTiming | null = null;
  const measure = (action: (t: ClientTiming) => void): void => {
    const current = timing;
    if (current === null) {
      return;
    }
    try {
      action(current);
    } catch (error) {
      console.warn(METRICS_TOPIC, error);
      timing = null;
      try {
        current.stop();
      } catch (stopError) {
        console.warn(METRICS_TOPIC, stopError);
      }
    }
  };
  const onState = (state: string): void => {
    measure((t) => t.agentState(state));
  };
  try {
    timing = new ClientTiming((m) => {
      room.localParticipant
        .sendText(JSON.stringify({ type: "client_e2e", ...m }), {
          topic: METRICS_TOPIC,
        })
        .catch((error) => console.warn(METRICS_TOPIC, error));
    });
  } catch (error) {
    console.warn(METRICS_TOPIC, error);
  }
  let credentials: { token: string; url: string };
  try {
    credentials = await requestToken(device);
  } catch (error) {
    measure((t) => t.stop());
    throw error;
  }
  const { token, url } = credentials;
  const room = new Room();
  room.registerTextStreamHandler(
    "lk.transcription",
    async (reader, { identity }) => {
      const attributes = reader.info.attributes ?? {};
      const segment = attributes["lk.segment_id"] ?? reader.info.id;
      const who = identity === room.localParticipant.identity ? "вы" : "агент";
      const final = attributes["lk.transcription_final"] === "true";
      let text = "";
      for await (const chunk of reader) {
        text += chunk;
        handlers.transcript({ segment, who, text, final });
      }
      handlers.transcript({
        segment,
        who,
        text,
        final: reader.info.attributes?.["lk.transcription_final"] === "true",
      });
    },
  );
  room.registerTextStreamHandler("voca.cards", async (reader) => {
    try {
      const message = JSON.parse(await reader.readAll());
      if (message.type === "state") {
        handlers.cards(message as CardsState);
      } else if (message.type === "tasks" && Array.isArray(message.tasks)) {
        handlers.tasks(message.tasks as TaskCard[]);
      } else if (message.type === "task") {
        handlers.task(message as TaskCard);
      }
    } catch (error) {
      console.warn("voca.cards", error);
    }
  });
  room.registerTextStreamHandler(HISTORY_TOPIC, async (reader) => {
    try {
      const message = JSON.parse(await reader.readAll());
      if (message.type === "history" && Array.isArray(message.items)) {
        handlers.history(message.items as HistoryItem[]);
      }
    } catch (error) {
      console.warn("voca.history", error);
    }
  });
  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind === Track.Kind.Audio) {
      document.body.append(track.attach());
      measure((t) => t.addAgent(track.mediaStreamTrack));
    }
  });
  room.on(RoomEvent.ParticipantAttributesChanged, (_changed, participant) => {
    reportAgent(participant, handlers, onState);
  });
  room.on(RoomEvent.ParticipantConnected, (participant) => {
    reportAgent(participant, handlers, onState);
  });
  room.on(RoomEvent.ConnectionStateChanged, (state) => {
    handlers.connection(state);
  });
  room.on(RoomEvent.Disconnected, (reason) => {
    measure((t) => t.stop());
    handlers.disconnected(reason);
  });
  try {
    await room.connect(url, token);
  } catch (error) {
    measure((t) => t.stop());
    throw error;
  }
  try {
    for (const participant of room.remoteParticipants.values()) {
      reportAgent(participant, handlers, onState);
    }
    const track = await createLocalAudioTrack({
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    });
    await room.localParticipant.publishTrack(track);
    measure((t) => t.start(track.mediaStreamTrack));
  } catch (error) {
    measure((t) => t.stop());
    await room.disconnect();
    throw error;
  }
  return room;
}

export async function disconnect(room: Room): Promise<void> {
  await room.disconnect();
}

export interface ActionResult {
  ok: boolean;
  error?: string;
}

export async function act(
  room: Room,
  method: "voca.approve" | "voca.cancel_task",
  payload: Record<string, string>,
): Promise<ActionResult> {
  const agent = [...room.remoteParticipants.values()].find(
    (participant) => participant.attributes[AGENT_STATE] !== undefined,
  );
  if (agent === undefined) {
    return { ok: false, error: "агент не в комнате" };
  }
  try {
    const response = await room.localParticipant.performRpc({
      destinationIdentity: agent.identity,
      method,
      payload: JSON.stringify(payload),
      responseTimeout: 45000,
    });
    return JSON.parse(response) as ActionResult;
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}
