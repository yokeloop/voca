import { ConnectionState, DisconnectReason, type Room } from "livekit-client";
import { connect, deviceName, disconnect, type Handlers } from "./room";
import {
  render,
  setCards,
  setConnected,
  setErrorLine,
  setState,
  stateLabel,
  upsertSegment,
} from "./ui";

const DELAYS = [1000, 2000, 4000, 8000, 15000];
const FINAL_REASONS = new Set<DisconnectReason>([
  DisconnectReason.DUPLICATE_IDENTITY,
  DisconnectReason.PARTICIPANT_REMOVED,
]);

const app = document.querySelector<HTMLElement>("#app");
if (app) {
  const button = render(app);
  let room: Room | undefined;
  let connection = ConnectionState.Disconnected;
  let agent: string | undefined;
  let wanted = false;
  let connecting = false;
  let generation = 0;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const update = () => setState(stateLabel(connection, agent));
  const stop = () => {
    wanted = false;
    connecting = false;
    generation += 1;
    clearTimeout(timer);
    timer = undefined;
    attempt = 0;
    setConnected(false);
  };
  const scheduleReconnect = () => {
    clearTimeout(timer);
    const delay = DELAYS[Math.min(attempt, DELAYS.length - 1)];
    attempt += 1;
    setState(`переподключение через ${delay / 1000} с (попытка ${attempt})`);
    timer = setTimeout(async () => {
      timer = undefined;
      if (!wanted) {
        return;
      }
      try {
        await open();
        attempt = 0;
      } catch (error) {
        console.warn("reconnect", error);
        if (wanted) {
          scheduleReconnect();
        }
      }
    }, delay);
  };
  const handlersFor = (current: number): Handlers => {
    const live = () => current === generation;
    return {
      transcript(transcript) {
        if (live()) {
          upsertSegment(transcript);
        }
      },
      cards(state) {
        if (live()) {
          setCards(state);
        }
      },
      agentState(state) {
        if (live()) {
          agent = state;
          update();
        }
      },
      connection(state) {
        if (!live()) {
          return;
        }
        connection = state;
        if (state === ConnectionState.Disconnected) {
          agent = undefined;
          room = undefined;
          setCards(undefined);
        }
        setConnected(wanted);
        update();
      },
      disconnected(reason) {
        if (!live()) {
          return;
        }
        if (reason !== undefined && FINAL_REASONS.has(reason)) {
          stop();
          setErrorLine(`отключено сервером: ${DisconnectReason[reason]}`);
          return;
        }
        if (wanted && !connecting && timer === undefined) {
          scheduleReconnect();
        }
      },
    };
  };
  async function open(): Promise<void> {
    generation += 1;
    const current = generation;
    connecting = true;
    try {
      const opened = await connect(deviceName(), handlersFor(current));
      if (current === generation && wanted) {
        room = opened;
      } else {
        await disconnect(opened);
      }
    } finally {
      if (current === generation) {
        connecting = false;
      }
    }
  }
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      if (!wanted) {
        wanted = true;
        setConnected(true);
        await open();
      } else {
        const current = room;
        room = undefined;
        stop();
        connection = ConnectionState.Disconnected;
        agent = undefined;
        setCards(undefined);
        update();
        if (current !== undefined) {
          await disconnect(current);
        }
      }
    } catch (error) {
      stop();
      room = undefined;
      setState(error instanceof Error ? error.message : String(error));
    } finally {
      button.disabled = false;
    }
  });
  update();
}
