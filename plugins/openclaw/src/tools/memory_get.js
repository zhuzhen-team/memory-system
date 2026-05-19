/**
 * memory_get 工具
 *
 * 取一条记忆的完整 markdown 内容 + frontmatter。
 *
 * memoryd `show <slug>` 输出 raw markdown（含 YAML frontmatter），
 * 直接透传给 agent；agent 端可以按需 yaml-parse。
 */

export function buildMemoryGetTool({ client, logger } = {}) {
  if (!client) throw new Error("memory_get: client is required");
  return {
    name: "memory_get",
    label: "Memory Get",
    description: "取一条记忆的完整 markdown 内容 + frontmatter。",
    inputSchema: {
      type: "object",
      properties: {
        memory_id: { type: "string", description: "memory_search 返回的 memory_id（slug）" },
      },
      required: ["memory_id"],
    },
    async execute(_toolCallId, params = {}) {
      const memoryId = params.memory_id;
      const res = await client.get(memoryId);
      if (!res.ok) {
        logger?.warn?.(`[memoryd] memory_get failed: ${res.error}`);
        return {
          content: [{ type: "text", text: `memory_get failed: ${res.error}` }],
        };
      }
      return {
        content: [{ type: "text", text: res.data || "" }],
      };
    },
  };
}
