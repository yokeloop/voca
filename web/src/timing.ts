export const TICK_MS = 10;
export const FFT_SIZE = 1024;
export const FLOOR_WINDOW_MS = 3000;
export const FLOOR_PERCENTILE = 0.1;
export const SPEECH_MARGIN_DB = 15;
export const PEAK_MARGIN_DB = 20;
export const MIN_SPEECH_MS = 150;
export const AGENT_AUDIBLE_DB = -50;
export const AGENT_ONSET_TICKS = 2;
export const MAX_CLIENT_MS = 30000;

const SILENT_DB = -120;

export interface ClientMeasure {
  seq: number;
  client_e2e_ms: number;
  speech_ms: number;
}

interface Probe {
  source: MediaStreamAudioSourceNode;
  analyser: AnalyserNode;
  buffer: Float32Array<ArrayBuffer>;
}

interface Frame {
  at: number;
  level: number;
}

export function levelDb(
  analyser: AnalyserNode,
  buffer: Float32Array<ArrayBuffer>,
): number {
  analyser.getFloatTimeDomainData(buffer);
  let sum = 0;
  for (const sample of buffer) {
    sum += sample * sample;
  }
  const power = sum / buffer.length;
  return power === 0 ? SILENT_DB : 10 * Math.log10(power);
}

function percentile(values: number[], q: number): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor((sorted.length - 1) * q)];
}

export class ClientTiming {
  private readonly context = new AudioContext();
  private readonly onMeasure: (m: ClientMeasure) => void;
  private mic: Probe | null = null;
  private agent: Probe | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private stopped = false;
  private seq = 0;
  private ring: number[] = [];
  private utterance: Frame[] = [];
  private state = "";
  private speechEnd: number | null = null;
  private speechMs = 0;
  private armedAt: number | null = null;
  private agentRun = 0;
  private agentRunStart = 0;

  constructor(onMeasure: (m: ClientMeasure) => void) {
    this.onMeasure = onMeasure;
  }

  start(mic: MediaStreamTrack): void {
    if (this.stopped) {
      return;
    }
    this.context.resume().catch((error) => console.warn("voca.metrics", error));
    this.mic = this.probe(mic);
    this.timer = setInterval(() => this.tick(), TICK_MS);
  }

  addAgent(track: MediaStreamTrack): void {
    if (this.stopped) {
      return;
    }
    this.agent?.source.disconnect();
    this.agent = this.probe(track);
  }

  agentState(state: string): void {
    const previous = this.state;
    this.state = state;
    if (state === previous) {
      return;
    }
    if (
      state === "thinking" ||
      (state === "speaking" && previous !== "thinking")
    ) {
      this.arm(performance.now(), state === "speaking");
    }
    if (state === "listening") {
      this.armedAt = null;
      this.speechEnd = null;
    }
    if (state === "speaking" || state === "listening") {
      this.utterance = [];
    }
  }

  stop(): void {
    if (this.stopped) {
      return;
    }
    this.stopped = true;
    if (this.timer !== null) {
      clearInterval(this.timer);
    }
    this.context.close().catch((error) => console.warn("voca.metrics", error));
  }

  private probe(track: MediaStreamTrack): Probe {
    const source = this.context.createMediaStreamSource(
      new MediaStream([track]),
    );
    const analyser = this.context.createAnalyser();
    analyser.fftSize = FFT_SIZE;
    source.connect(analyser);
    return { source, analyser, buffer: new Float32Array(FFT_SIZE) };
  }

  private arm(now: number, speaking: boolean): void {
    this.armedAt = null;
    this.speechEnd = null;
    if (this.utterance.length > 0) {
      const floor = percentile(this.ring, FLOOR_PERCENTILE) + SPEECH_MARGIN_DB;
      const peak = Math.max(...this.utterance.map((frame) => frame.level));
      const threshold = Math.max(floor, peak - PEAK_MARGIN_DB);
      const loud = this.utterance.filter((frame) => frame.level > threshold);
      if (loud.length > 0) {
        const first = loud[0].at;
        const last = loud[loud.length - 1].at;
        if (last - first >= MIN_SPEECH_MS) {
          this.speechEnd = last;
          this.speechMs = last - first;
          this.armedAt = now;
        }
      }
    }
    this.utterance = [];
    if (
      speaking &&
      this.armedAt !== null &&
      this.agentRun >= AGENT_ONSET_TICKS
    ) {
      this.onset(this.agentRunStart);
    }
  }

  private tick(): void {
    const now = performance.now();
    if (this.mic !== null) {
      const level = levelDb(this.mic.analyser, this.mic.buffer);
      this.ring.push(level);
      if (this.ring.length > FLOOR_WINDOW_MS / TICK_MS) {
        this.ring.shift();
      }
      if (this.state !== "speaking") {
        this.utterance.push({ at: now, level });
        if (this.utterance.length > MAX_CLIENT_MS / TICK_MS) {
          this.utterance.shift();
        }
      }
    }
    if (this.agent !== null) {
      this.tickAgent(now, levelDb(this.agent.analyser, this.agent.buffer));
    }
  }

  private tickAgent(now: number, level: number): void {
    if (level <= AGENT_AUDIBLE_DB) {
      this.agentRun = 0;
      return;
    }
    if (this.agentRun === 0) {
      this.agentRunStart = now;
    }
    this.agentRun += 1;
    if (
      this.agentRun < AGENT_ONSET_TICKS ||
      this.armedAt === null ||
      this.agentRunStart < this.armedAt
    ) {
      return;
    }
    this.onset(this.agentRunStart);
  }

  private onset(onset: number): void {
    const speechEnd = this.speechEnd;
    if (
      speechEnd !== null &&
      onset > speechEnd &&
      onset - speechEnd <= MAX_CLIENT_MS
    ) {
      this.seq += 1;
      this.onMeasure({
        seq: this.seq,
        client_e2e_ms: Math.round(onset - speechEnd),
        speech_ms: Math.round(this.speechMs),
      });
    }
    this.speechEnd = null;
    this.armedAt = null;
  }
}
