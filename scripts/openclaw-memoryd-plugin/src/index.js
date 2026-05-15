/**
 * OpenClaw plugin entry. Loaded by `openclaw plugins install --force .`
 *
 * Indirection via register.js lets us unit-test the subscription shape
 * without needing the SDK installed in this repo's node_modules.
 */

import { definePluginEntry } from "@openclaw/plugin-sdk";
import { makeSubscription } from "./register.js";

// NOTE: NO `kind` field. Setting kind:"memory" would put us in the exclusive
// memory slot and disable OpenClaw's stock memory-core — violates spec §8
// "不接管三端原生记忆机制". We coexist as a non-exclusive event observer.
export default definePluginEntry({
  id: "memoryd-openclaw",
  name: "memoryd OpenClaw bridge",
  description: "Mirror OpenClaw turn-end events into the local memoryd data root.",
  register(api) {
    api.registerAgentEventSubscription(makeSubscription());
  },
});
