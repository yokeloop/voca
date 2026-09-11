import test from "node:test";
import assert from "node:assert/strict";

import { isCursor } from "../src/pages/session-console/lib/identifiers.ts";
import { parseServerEvent } from "../src/pages/session-console/api/protocol.ts";
import {
  applyServerEvent,
  resetProjection,
} from "../src/pages/session-console/model/projection.ts";

const sessionId = "f1d37af1-2ed6-4bb2-a7a2-5d0777d1db4c";

function event(type, eventId, extra = {}) {
  return JSON.stringify({
    type,
    session_id: sessionId,
    event_id: eventId,
    ...(type === "session.snapshot"
      ? {
          connection: "disconnected",
          conversation: "off",
          active_turn: null,
          messages: [],
          tasks: [],
          deliveries: [],
        }
      : {
          component: "control",
          code: "invalid_cursor",
          message: "Invalid cursor",
        }),
    ...extra,
  });
}

test("projection applies ordered snapshots and errors without losing precision", () => {
  let projection = resetProjection(sessionId);
  const snapshot = parseServerEvent(event("session.snapshot", "e9"), sessionId);
  assert.ok(snapshot);
  projection = applyServerEvent(projection, snapshot);
  const error = parseServerEvent(event("error", "e10"), sessionId);
  assert.ok(error);
  projection = applyServerEvent(projection, error);
  assert.equal(projection.lastEventId, "e10");
  assert.equal(projection.error?.code, "invalid_cursor");
  assert.ok(projection.snapshot);
  const large = parseServerEvent(
    event("session.snapshot", "e9007199254740992"),
    sessionId,
  );
  assert.ok(large);
  projection = applyServerEvent(projection, large);
  assert.equal(projection.lastEventId, "e9007199254740992");
  assert.equal(applyServerEvent(projection, error), projection);
  const foreign = parseServerEvent(
    event("session.snapshot", "e11", {
      session_id: "a1d37af1-2ed6-4bb2-a7a2-5d0777d1db4c",
    }),
    sessionId,
  );
  assert.equal(foreign, null);
});

test("requested future cursor is not a projection watermark", () => {
  let projection = resetProjection(sessionId);
  const error = parseServerEvent(event("error", "e1"), sessionId);
  assert.ok(error);
  projection = applyServerEvent(projection, error);
  assert.equal(projection.lastEventId, "e1");
  projection = resetProjection(sessionId);
  const snapshot = parseServerEvent(event("session.snapshot", "e2"), sessionId);
  assert.ok(snapshot);
  assert.equal(applyServerEvent(projection, snapshot).lastEventId, "e2");
});

test("cursor validation rejects malformed and out-of-range wire values", () => {
  assert.equal(isCursor("e١"), false);
  assert.equal(isCursor("e0"), false);
  assert.equal(isCursor("e9223372036854775808"), false);
  assert.equal(
    parseServerEvent(
      event("session.snapshot", "e9223372036854775808"),
      sessionId,
    ),
    null,
  );
});
