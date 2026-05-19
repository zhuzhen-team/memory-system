/**
 * 工具注册聚合
 *
 * 通过 `registerTools(api, { client })` 把 3 个工具一次性挂到 OpenClaw plugin
 * api 上。也导出 `buildTools` —— 给测试用：不依赖 SDK 也能拿到 3 个工具对象。
 */

import { buildMemorySearchTool } from "./memory_search.js";
import { buildMemoryGetTool } from "./memory_get.js";
import { buildMemoryTranscriptTool } from "./memory_transcript.js";

export function buildTools({ client, logger } = {}) {
  return [
    buildMemorySearchTool({ client, logger }),
    buildMemoryGetTool({ client, logger }),
    buildMemoryTranscriptTool({ client, logger }),
  ];
}

export function registerTools(api, { client, logger } = {}) {
  if (!api || typeof api.registerTool !== "function") {
    throw new Error("registerTools: api.registerTool is not available");
  }
  const tools = buildTools({ client, logger });
  for (const tool of tools) {
    // 工厂形式：保持和 memsearch 风格一致；ctx 暂未使用（identity 已经由 client 解决）
    api.registerTool(() => tool, { name: tool.name });
  }
  return tools;
}

export { buildMemorySearchTool, buildMemoryGetTool, buildMemoryTranscriptTool };
