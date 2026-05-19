/**
 * OpenClaw plugin entry. Loaded by `openclaw plugins install --force .`
 *
 * 现在是 v0.3 native plugin 形态：
 *   - 3 工具：memory_search / memory_get / memory_transcript
 *   - 2 hook：before_agent_start / agent_end
 *   - 仍保留 lifecycle 事件桥接（兜底；当 hook 不可用时）
 *
 * 间接走 register.js / tools/index.js / hooks/* 让单元测试可以脱离 SDK 跑。
 */

// OpenClaw 2026.5.7+ deprecates bare "@openclaw/plugin-sdk" compat import;
// use focused subpath instead (warning at install time guides this).
import { definePluginEntry } from "@openclaw/plugin-sdk/plugin-entry";
import { makeSubscription } from "./register.js";
import { createMemorydClient } from "./memoryd_client.js";
import { registerTools } from "./tools/index.js";
import { buildBeforeAgentStartHook } from "./hooks/before_agent_start.js";
import { buildAgentEndHook } from "./hooks/agent_end.js";

// NOTE: NO `kind` field. Setting kind:"memory" would put us in the exclusive
// memory slot and disable OpenClaw's stock memory-core — violates spec §8
// "不接管三端原生记忆机制". We coexist as a non-exclusive event observer.
export default definePluginEntry({
  id: "memoryd-openclaw",
  name: "memoryd OpenClaw bridge",
  description:
    "Native plugin: memory_search/get/transcript 工具 + before_agent_start/agent_end hook，" +
    "把 OpenClaw 接入本地 memoryd 后端。",
  register(api) {
    const cfg = (api && api.pluginConfig) || {};
    const logger = api && api.logger;

    const client = createMemorydClient({
      mode: cfg.transport === "http" ? "http" : "cli",
      binPath: cfg.binPath,
      port: cfg.port,
    });

    // 工具注册（如果 SDK 不支持 registerTool 会抛，但不影响 lifecycle 桥接）
    try {
      registerTools(api, { client, logger });
    } catch (e) {
      logger?.warn?.(`[memoryd] registerTools skipped: ${e?.message || e}`);
    }

    // hook 注册（基于 api.on(eventName, handler)，参考 memsearch 模式）
    const autoCapture = cfg.autoCapture !== false;
    const autoRecall = cfg.autoRecall !== false;
    let hookRegistered = false;
    if (typeof api?.on === "function") {
      if (autoRecall) {
        api.on("before_agent_start", buildBeforeAgentStartHook({ client, logger }));
      }
      if (autoCapture) {
        api.on("agent_end", buildAgentEndHook({ client, logger }));
        hookRegistered = true;
      }
    }

    // Fallback：旧版本 SDK 没有 agent_end 事件 → 仍订阅 lifecycle 桥接
    if (!hookRegistered && typeof api?.registerAgentEventSubscription === "function") {
      api.registerAgentEventSubscription(makeSubscription());
    }

    logger?.info?.(
      `[memoryd] plugin loaded (transport=${client.mode}, ` +
      `autoCapture=${autoCapture}, autoRecall=${autoRecall})`
    );
  },
});
