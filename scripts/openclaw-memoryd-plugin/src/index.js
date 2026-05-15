/**
 * OpenClaw plugin entry. Loaded by `openclaw plugins install --force .`
 *
 * Indirection via register.js lets us unit-test the subscription shape
 * without needing the SDK installed in this repo's node_modules.
 */

// OpenClaw 2026.5.7+ deprecates bare "@openclaw/plugin-sdk" compat import;
// use focused subpath instead (warning at install time guides this).
import { definePluginEntry } from "@openclaw/plugin-sdk/plugin-entry";
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
