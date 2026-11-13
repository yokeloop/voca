import { ConnectionState, type Room } from "livekit-client";
import { connect, deviceName, disconnect, type Handlers } from "./room";
import {
  render,
  setCards,
  setConnected,
  setState,
  stateLabel,
  upsertSegment,
} from "./ui";

const DELAYS = [1000, 2000, 4000, 8000, 15000];

const app = document.querySelector<HTMLElement>("#app");
if (app) {
  const button = render(app);
  let room: Room | undefined;
  let connection = ConnectionState.Disconnected;
  let agent: string | undefined;
  let wanted = false;
  let connecting = false;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const update = () => setState(stateLabel(connection, agent));
  const open = async (): Promise<void> => {
    connecting = true;
    try {
      const opened = await connect(deviceName(), handlers);
      if (wanted) {
        room = opened;
      } else {
        await disconnect(opened);
      }
    } finally {
      connecting = false;
    }
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
  const handlers: Handlers = {
    transcript: upsertSegment,
    cards: setCards,
    agentState(state) {
      agent = state;
      update();
    },
    connection(state) {
      connection = state;
      if (state === ConnectionState.Disconnected) {
        agent = undefined;
        room = undefined;
        setCards(undefined);
      }
      setConnected(wanted);
      update();
      if (
        state === ConnectionState.Disconnected &&
        wanted &&
        !connecting &&
        timer === undefined
      ) {
        scheduleReconnect();
      }
    },
  };
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      if (!wanted) {
        wanted = true;
        setConnected(true);
        await open();
      } else {
        wanted = false;
        clearTimeout(timer);
        timer = undefined;
        attempt = 0;
        setConnected(false);
        if (room !== undefined) {
          const current = room;
          room = undefined;
          await disconnect(current);
        } else {
          update();
        }
      }
    } catch (error) {
      wanted = false;
      clearTimeout(timer);
      timer = undefined;
      attempt = 0;
      room = undefined;
      setConnected(false);
      setState(error instanceof Error ? error.message : String(error));
    } finally {
      button.disabled = false;
    }
  });
  update();
}
