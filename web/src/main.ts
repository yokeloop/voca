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

const app = document.querySelector<HTMLElement>("#app");
if (app) {
  const button = render(app);
  let room: Room | undefined;
  let connection = ConnectionState.Disconnected;
  let agent: string | undefined;
  const update = () => setState(stateLabel(connection, agent));
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
      }
      setConnected(state !== ConnectionState.Disconnected);
      update();
    },
  };
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      if (room === undefined) {
        room = await connect(deviceName(), handlers);
      } else {
        const current = room;
        room = undefined;
        await disconnect(current);
      }
    } catch (error) {
      room = undefined;
      setConnected(false);
      setState(error instanceof Error ? error.message : String(error));
    } finally {
      button.disabled = false;
    }
  });
  update();
}
