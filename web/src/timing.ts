export const TICK_MS = 10;
export const FFT_SIZE = 1024;
export const FLOOR_WINDOW_MS = 3000;
export const FLOOR_PERCENTILE = 0.1;
export const SPEECH_MARGIN_DB = 15;
export const MIN_SPEECH_MS = 150;
export const HANGOVER_MS = 300;
export const AGENT_AUDIBLE_DB = -50;
export const AGENT_ONSET_TICKS = 2;
export const AGENT_QUIET_MS = 300;
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
  private speechStart: number | null = null;
  private lastLoud = 0;
  private agentAtLastLoud = false;
  private speechEnd: number | null = null;
  private speechMs = 0;
  private agentRun = 0;
  private agentRunStart = 0;
  private agentQuietBefore = Number.POSITIVE_INFINITY;
  private agentLastHeard = Number.NEGATIVE_INFINITY;
  private agentAudible = false;

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

  private tick(): void {
    const now = performance.now();
    if (this.agent !== null) {
      this.tickAgent(now, levelDb(this.agent.analyser, this.agent.buffer));
    }
    if (this.mic !== null) {
      this.tickMic(now, levelDb(this.mic.analyser, this.mic.buffer));
    }
  }

  private tickMic(now: number, level: number): void {
    this.ring.push(level);
    if (this.ring.length > FLOOR_WINDOW_MS / TICK_MS) {
      this.ring.shift();
    }
    const floor = percentile(this.ring, FLOOR_PERCENTILE);
    if (level > floor + SPEECH_MARGIN_DB) {
      if (this.speechStart === null) {
        this.speechStart = now;
      }
      this.lastLoud = now;
      this.agentAtLastLoud = this.agentAudible;
      return;
    }
    if (this.speechStart === null || now - this.lastLoud < HANGOVER_MS) {
      return;
    }
    if (
      this.lastLoud - this.speechStart >= MIN_SPEECH_MS &&
      !this.agentAtLastLoud
    ) {
      this.speechEnd = this.lastLoud;
      this.speechMs = this.lastLoud - this.speechStart;
    }
    this.speechStart = null;
  }

  private tickAgent(now: number, level: number): void {
    if (level <= AGENT_AUDIBLE_DB) {
      this.agentRun = 0;
      this.agentAudible = false;
      return;
    }
    if (this.agentRun === 0) {
      this.agentRunStart = now;
      this.agentQuietBefore = now - this.agentLastHeard;
    }
    this.agentRun += 1;
    this.agentLastHeard = now;
    if (this.agentRun !== AGENT_ONSET_TICKS) {
      return;
    }
    this.agentAudible = true;
    if (this.agentQuietBefore >= AGENT_QUIET_MS) {
      this.onset(this.agentRunStart);
    }
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
  }
}
