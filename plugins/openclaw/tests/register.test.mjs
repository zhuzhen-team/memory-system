import { test } from "node:test";
import assert from "node:assert/strict";
import { makeHandler, makeSubscription } from "../src/register.js";

test("subscription has expected shape", () => {
  const sub = makeSubscription();
  assert.equal(sub.id, "memoryd-capture");
  assert.deepEqual(sub.streams, ["lifecycle"]);
  assert.equal(typeof sub.handle, "function");
});

test("handler invokes spawn when event has messages", async () => {
  const spawned = [];
  const handler = makeHandler({
    spawn: (payload) => spawned.push(payload),
    log: () => {},  // suppress diagnostic log writes during test
  });
  await handler({
    stream: "lifecycle",
    sessionId: "s1",
    cwd: "/tmp/a",
    messages: [{ role: "user", content: "hi" }],
  });
  assert.equal(spawned.length, 1);
  assert.equal(spawned[0].session_id, "s1");
});

test("handler skips spawn for irrelevant events", async () => {
  const spawned = [];
  const handler = makeHandler({
    spawn: (payload) => spawned.push(payload),
    log: () => {},
  });
  await handler({ stream: "lifecycle", type: "agent_start" });
  await handler({ stream: "lifecycle" });
  // Empty messages without cwd should also be skipped (heartbeat shape).
  await handler({ stream: "lifecycle", messages: [] });
  assert.equal(spawned.length, 0);
});

test("handler logs every event regardless of spawn", async () => {
  const logged = [];
  const handler = makeHandler({
    spawn: () => {},
    log: (_path, line) => logged.push(line),
  });
  await handler({ stream: "lifecycle", type: "x" });
  await handler({ stream: "lifecycle", cwd: "/y", messages: [] });
  assert.equal(logged.length, 2);
  assert.ok(logged[0].includes("\"stream\":\"lifecycle\""));
});

test("handler swallows errors thrown by spawn", async () => {
  const handler = makeHandler({
    spawn: () => { throw new Error("boom"); },
    log: () => {},
  });
  // Should not reject:
  await handler({ stream: "lifecycle", cwd: "/x", messages: [{ role: "user", content: "h" }] });
});
