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

export interface Handlers {
  transcript(transcript: Transcript): void;
  cards(state: CardsState): void;
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
  timing: ClientTiming,
): void {
  const state = participant.attributes[AGENT_STATE];
  if (state !== undefined) {
    timing.agentState(state);
    handlers.agentState(state);
  }
}

export async function connect(
  device: string,
  handlers: Handlers,
): Promise<Room> {
  const timing = new ClientTiming((m) => {
    room.localParticipant
      .sendText(JSON.stringify({ type: "client_e2e", ...m }), {
        topic: METRICS_TOPIC,
      })
      .catch((error) => console.warn(METRICS_TOPIC, error));
  });
  const response = await fetch("/api/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device }),
  });
  if (!response.ok) {
    timing.stop();
    throw new Error(`/api/token: ${response.status}`);
  }
  const { token, url } = (await response.json()) as {
    token: string;
    url: string;
  };
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
      }
    } catch (error) {
      console.warn("voca.cards", error);
    }
  });
  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind === Track.Kind.Audio) {
      document.body.append(track.attach());
      timing.addAgent(track.mediaStreamTrack);
    }
  });
  room.on(RoomEvent.ParticipantAttributesChanged, (_changed, participant) => {
    reportAgent(participant, handlers, timing);
  });
  room.on(RoomEvent.ParticipantConnected, (participant) => {
    reportAgent(participant, handlers, timing);
  });
  room.on(RoomEvent.ConnectionStateChanged, (state) => {
    handlers.connection(state);
  });
  room.on(RoomEvent.Disconnected, (reason) => {
    timing.stop();
    handlers.disconnected(reason);
  });
  try {
    await room.connect(url, token);
  } catch (error) {
    timing.stop();
    throw error;
  }
  try {
    for (const participant of room.remoteParticipants.values()) {
      reportAgent(participant, handlers, timing);
    }
    const track = await createLocalAudioTrack({
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    });
    await room.localParticipant.publishTrack(track);
    timing.start(track.mediaStreamTrack);
  } catch (error) {
    timing.stop();
    await room.disconnect();
    throw error;
  }
  return room;
}

export async function disconnect(room: Room): Promise<void> {
  await room.disconnect();
}
