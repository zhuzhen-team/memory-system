/**
 * memory_search 工具
 *
 * 在用户的本地记忆系统中检索相关记忆。
 * 返回 OpenClaw 工具协议的标准 content 数组：
 *   { content: [{ type: "text", text: <JSON string> }] }
 *
 * 上游调用方（OpenClaw agent）会把 text 直接喂给模型，所以这里输出
 * 紧凑 JSON（不要 indent）以省 token。
 */

export function buildMemorySearchTool({ client, logger } = {}) {
  if (!client) {
    throw new Error("memory_search: client is required");
  }
  return {
    name: "memory_search",
    label: "Memory Search",
    description: "在用户的本地记忆系统中检索相关记忆。",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "自然语言查询，支持中文" },
        top_k: { type: "integer", default: 10 },
        scope: { type: "string", default: "auto" },
      },
      required: ["query"],
    },
    async execute(_toolCallId, params = {}) {
      const query = params.query;
      const topK = Number.isFinite(params.top_k) ? params.top_k : 10;
      const scope = params.scope || "auto";
      const res = await client.search({ query, top_k: topK, scope });
      if (!res.ok) {
        logger?.warn?.(`[memoryd] memory_search failed: ${res.error}`);
        return {
          content: [{ type: "text", text: JSON.stringify({ error: res.error, results: [] }) }],
        };
      }
      // memoryd CLI 输出格式：[{ slug, title, scope_hash, excerpt, path }, ...]
      // 规范化成签名里要求的字段。
      const normalized = (Array.isArray(res.data) ? res.data : []).map((h) => ({
        memory_id: h.memory_id || h.slug || h.id || "",
        content_preview: h.content_preview || h.excerpt || h.title || "",
        scope: h.scope || h.scope_hash || "",
        created_at: h.created_at || null,
        dura_score: typeof h.dura_score === "number" ? h.dura_score : null,
        source: h.source || null,
      }));
      return {
        content: [{ type: "text", text: JSON.stringify(normalized) }],
      };
    },
  };
}
