/**
 * agent_end hook
 *
 * 一轮 agent 结束（OpenClaw 触发 agent_end 事件）后：
 *   1. 从 event.messages 抽出最后 N 轮对话
 *   2. 喂给 LLM 生成三人称要点摘要
 *   3. 调 memoryd capture --source=openclaw 写入
 *
 * 全部异步（fire-and-forget）—— 不阻塞 OpenClaw 主流程；任何失败仅 log。
 *
 * 需要权限：allowConversationAccess=true（否则 event.messages 拿不到）
 *
 * 注意：这取代了现有 register.js 里基于 lifecycle.spawnCapture 的逻辑——
 * 入口 index.js 会判断 hook 是否启用，若启用则不再走 lifecycle 路径。
 */

const MIN_TURN_CHARS = 50;
const MAX_TURN_CHARS = 6000;
const DEFAULT_TURNS = 4;

/** 从 messages 数组抽最后 N 轮（user+assistant 对）拼成文本。 */
export function extractRecentTurns(messages, { maxTurns = DEFAULT_TURNS } = {}) {
  if (!Array.isArray(messages) || messages.length === 0) return "";
  const turns = [];
  for (let i = messages.length - 1; i >= 0 && turns.length < maxTurns * 2; i--) {
    const m = messages[i];
    if (!m || typeof m !== "object") continue;
    const role = m.role || m.author || m.from || (m.message && m.message.role) || "";
    const content = m.content ?? m.text ?? (m.message && m.message.content);
    const text = stringifyContent(content);
    if (!text || text.length < 5) continue;
    if (role === "user" || role === "human") {
      turns.unshift(`[Human]: ${text}`);
    } else if (role === "assistant" || role === "agent") {
      turns.unshift(`[Assistant]: ${text.slice(0, MAX_TURN_CHARS)}`);
    }
  }
  return turns.join("\n\n");
}

function stringifyContent(content) {
  if (content == null) return "";
  if (typeof content === "string") return content.trim();
  if (Array.isArray(content)) {
    return content
      .map((c) => {
        if (typeof c === "string") return c;
        if (c?.type === "text" && typeof c.text === "string") return c.text;
        if (typeof c?.text === "string") return c.text;
        return "";
      })
      .filter(Boolean)
      .join("\n")
      .trim();
  }
  if (typeof content === "object" && typeof content.text === "string") return content.text.trim();
  return "";
}

/**
 * 朴素摘要兜底——LLM 不可用时直接截前 1500 字。和 memsearch 同款策略。
 */
export function naiveSummarize(text, { maxChars = 1500 } = {}) {
  if (!text) return "";
  const trimmed = text.trim();
  if (trimmed.length <= maxChars) return trimmed;
  return trimmed.slice(0, maxChars) + "\n...";
}

/**
 * 构造 hook 处理器。
 *
 * @param {object} opts
 * @param {object} opts.client                  memoryd 客户端
 * @param {Function} [opts.summarize]           注入式 LLM 摘要器（async (text) => string）
 * @param {object}   [opts.logger]
 * @param {boolean}  [opts.autoCapture=true]
 */
export function buildAgentEndHook({ client, summarize, logger, autoCapture = true } = {}) {
  if (!client) throw new Error("agent_end: client is required");
  const summarizer = summarize || (async (t) => naiveSummarize(t));

  return async function agent_end(event = {}) {
    if (!autoCapture) return;
    try {
      const messages = event.messages || event.transcript || [];
      const turnText = extractRecentTurns(messages);
      if (!turnText || turnText.length < MIN_TURN_CHARS) {
        logger?.info?.(`[memoryd] agent_end skipped (turn too short: ${turnText.length} chars)`);
        return;
      }

      // fire-and-forget: 不 await 整个流程，但内部仍处理异常
      doSummarizeAndCapture({
        client,
        summarizer,
        logger,
        turnText,
        sessionId: event.sessionId || event.session_id,
        cwd: event.cwd || event.workspace?.cwd,
      }).catch((e) => {
        logger?.warn?.(`[memoryd] agent_end async path failed: ${e?.message || e}`);
      });
    } catch (e) {
      logger?.warn?.(`[memoryd] agent_end sync setup failed: ${e?.message || e}`);
    }
  };
}

async function doSummarizeAndCapture({ client, summarizer, logger, turnText, sessionId, cwd }) {
  let summary;
  try {
    summary = await summarizer(turnText);
  } catch (e) {
    logger?.warn?.(`[memoryd] summarize failed, falling back to naive: ${e?.message || e}`);
    summary = naiveSummarize(turnText);
  }
  if (!summary || summary.trim().length < 10) {
    logger?.info?.(`[memoryd] agent_end skipped (empty summary)`);
    return;
  }
  const res = await client.capture({
    content: summary,
    source: "openclaw",
    session_id: sessionId,
    cwd,
    type: "session",
  });
  if (res.ok) {
    logger?.info?.(`[memoryd] agent_end captured ${summary.length} chars summary`);
  } else {
    logger?.warn?.(`[memoryd] agent_end capture failed: ${res.error}`);
  }
}
