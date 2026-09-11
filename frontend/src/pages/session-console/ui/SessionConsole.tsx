import { useSessionConsole } from "../model/use-session-console";

export function SessionConsole() {
  const model = useSessionConsole();
  return (
    <main>
      <h1>VOCA diagnostic session</h1>
      <p>Voice is not implemented yet.</p>
      <section>
        <button
          type="button"
          disabled={model.creating}
          onClick={() => void model.handleCreate()}
        >
          {model.creating ? "Creating session…" : "Create session"}
        </button>
        <button
          type="button"
          disabled={model.projection === null}
          onClick={model.reconnect}
        >
          Reconnect
        </button>
        <button
          type="button"
          disabled={model.session === null}
          onClick={model.checkStart}
        >
          Check start
        </button>
      </section>
      <section>
        <label>
          Session ID
          <input
            value={model.sessionInput}
            onChange={(event) => model.setSessionInput(event.target.value)}
          />
        </label>
        <label>
          Last cursor
          <input
            value={model.cursorInput}
            onChange={(event) => model.setCursorInput(event.target.value)}
            placeholder="empty for null"
          />
        </label>
        <button type="button" onClick={model.restore}>
          Restore
        </button>
      </section>
      <dl>
        <dt>Control transport</dt>
        <dd>{model.transport}</dd>
        <dt>Voice connection</dt>
        <dd>disconnected</dd>
        <dt>Conversation</dt>
        <dd>off</dd>
        <dt>Session ID</dt>
        <dd>{model.session?.sessionId ?? "none"}</dd>
        <dt>Cursor</dt>
        <dd>{model.projection?.lastEventId ?? "null"}</dd>
      </dl>
      {model.error !== null && <p role="alert">{model.error}</p>}
      <section>
        <h2>Last snapshot</h2>
        <pre>
          {model.projection?.snapshot === null || model.projection === null
            ? "No snapshot received."
            : JSON.stringify(model.projection.snapshot, null, 2)}
        </pre>
      </section>
    </main>
  );
}
