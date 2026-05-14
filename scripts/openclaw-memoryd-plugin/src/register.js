/**
 * Plugin registration logic — no OpenClaw SDK imports, so tests can
 * exercise this module directly with a mock api.
 *
 * Strategy: subscribe to OpenClaw's `lifecycle` event stream. For each
 * received event, attempt to build a memoryd capture payload. If the
 * event carries `cwd` or `messages` we spawn capture; otherwise we just
 * append a one-line marker to the events log (so Phase 1 user can
 * inspect what OpenClaw actually emits and we can narrow the filter in
 * a follow-up plan).
 */

import { appendFileSync } from "node:fs";
import { buildPayload, logFor, spawnCapture, tsLine } from "./payload.js";

export function makeHandler({ spawn = spawnCapture, log = appendFileSync } = {}) {
  return async (event, _ctx) => {
    try {
      const logPath = logFor();
      // Always log the event type (cheap diagnostic for Phase 1 tuning)
      const evtSummary = JSON.stringify({
        ts: new Date().toISOString(),
        stream: event?.stream,
        type: event?.type || event?.data?.type,
        hasMessages: Array.isArray(event?.messages),
        hasCwd: typeof event?.cwd === "string",
      });
      log(logPath, evtSummary + "\n");

      // Capture only when there's something to capture.
      // Empty messages arrays are common in lifecycle heartbeats; require
      // at least one message OR a cwd string.
      const looksCapturable =
        (Array.isArray(event?.messages) && event.messages.length > 0) ||
        typeof event?.cwd === "string";
      if (!looksCapturable) return;

      const payload = buildPayload(event);
      spawn(payload);
    } catch (err) {
      try {
        appendFileSync(logFor(), tsLine(`handler error: ${err?.message || err}`));
      } catch (_) {
        /* swallow */
      }
    }
  };
}

export function makeSubscription({ id = "memoryd-capture", spawn, log } = {}) {
  return {
    id,
    description: "Mirror OpenClaw turn-end events into memoryd",
    streams: ["lifecycle"],
    handle: makeHandler({ spawn, log }),
  };
}
