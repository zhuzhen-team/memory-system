/**
 * OpenClaw plugin entry. Loaded by `openclaw plugins install --force .`
 *
 * Indirection via register.js lets us unit-test the subscription shape
 * without needing the SDK installed in this repo's node_modules.
 */

import { definePluginEntry } from "@openclaw/plugin-sdk";
import { makeSubscription } from "./register.js";

export default definePluginEntry({
  id: "memoryd-openclaw",
  name: "memoryd OpenClaw bridge",
  description: "Mirror OpenClaw turn-end events into the local memoryd data root.",
  kind: "memory",
  register(api) {
    api.registerAgentEventSubscription(makeSubscription());
  },
});
