import { version } from "livekit-client";

const app = document.querySelector<HTMLElement>("#app");
if (app) {
  const title = document.createElement("h1");
  title.textContent = "VOCA";
  const line = document.createElement("p");
  line.textContent = `livekit-client ${version}`;
  app.replaceChildren(title, line);
}
