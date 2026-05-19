/**
 * memory_transcript 工具
 *
 * 取这条记忆所属 session 的完整 transcript（如可用）。
 *
 * 当 memoryd 后端找不到 transcript 时，返回提示文本但不抛错——
 * agent 看到说明就会主动换工具/换路径。
 */

export function buildMemoryTranscriptTool({ client, logger } = {}) {
  if (!client) throw new Error("memory_transcript: client is required");
  return {
    name: "memory_transcript",
    label: "Memory Transcript",
    description: "取这条记忆所属 session 的完整 transcript（如可用）。",
    inputSchema: {
      type: "object",
      properties: {
        memory_id: { type: "string", description: "memory_search 返回的 memory_id（slug）" },
      },
      required: ["memory_id"],
    },
    async execute(_toolCallId, params = {}) {
      const memoryId = params.memory_id;
      const res = await client.transcript(memoryId);
      if (!res.ok) {
        logger?.warn?.(`[memoryd] memory_transcript failed: ${res.error}`);
        return {
          content: [{
            type: "text",
            text: `transcript unavailable: ${res.error}`,
          }],
        };
      }
      const text = (res.data || "").trim();
      if (!text) {
        return {
          content: [{ type: "text", text: "transcript empty or not preserved for this memory." }],
        };
      }
      return {
        content: [{ type: "text", text }],
      };
    },
  };
}
