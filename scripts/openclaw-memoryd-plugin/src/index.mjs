/**
 * OpenClaw plugin: capture agent_end turns into memoryd.
 *
 * Registers on `agent_end` lifecycle hook. Receives a turn record from
 * OpenClaw's SDK and converts it into the same JSON payload shape that
 * `memoryd capture` accepts on stdin (session_id / transcript_path / cwd),
 * then spawns `memoryd capture --source openclaw`.
 *
 * Because memoryd's CLI reads transcript content from a JSONL file
 * (see _read_transcript_text in memoryd/src/memoryd/cli.py), this plugin
 * materializes OpenClaw's inline messages into a tmp JSONL in the same
 * format Claude Code emits ({"type":"user|assistant","message":{"content":
 * [{"type":"text","text":...}]}}). That way Plan 1's existing parser
 * sees a normal transcript and writes a real summary, not a stub.
 *
 * Requires `plugins.entries.<this-plugin>.hooks.allowConversationAccess = true`
 * for OpenClaw's SDK to actually deliver message content. Without it,
 * `event.messages` will be empty/redacted; we fall back to a stub session
 * tagged source=openclaw so the data root still records that OpenClaw was active.
 */

import { spawn } from "node:child_process";
import { mkdirSync, appendFileSync, existsSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

const DEFAULT_MEMORYD_BIN =
  "/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd";

function logFor() {
  const dataRoot =
    process.env.MEMORYD_DATA_ROOT || join(homedir(), ".local", "share", "memoryd");
  const logDir = join(dataRoot, "logs");
  if (!existsSync(logDir)) mkdirSync(logDir, { recursive: true });
  return join(logDir, "openclaw-agent-end.log");
}

function tsLine(extra) {
  return `${new Date().toISOString()}  ${extra}\n`;
}

/**
 * Normalize one OpenClaw message into the CC transcript JSONL shape.
 * OpenClaw's payload schema isn't fully documented; we accept a few
 * shapes and bail to null if we can't extract text.
 */
function normalizeMessage(m) {
  if (!m || typeof m !== "object") return null;
  const role = m.role || m.author || m.from || "user";
  const type = role === "assistant" || role === "agent" ? "assistant" : "user";

  let text = null;
  if (typeof m.content === "string") {
    text = m.content;
  } else if (Array.isArray(m.content)) {
    text = m.content
      .map((c) => (typeof c === "string" ? c : c?.text || c?.value || ""))
      .filter(Boolean)
      .join("\n");
  } else if (typeof m.text === "string") {
    text = m.text;
  }
  if (!text) return null;

  return {
    type,
    message: { content: [{ type: "text", text }] },
  };
}

/**
 * If `event.messages` (or .turns, .conversation, .history) has inline content,
 * write a tmp JSONL in CC's transcript format and return its path.
 * Otherwise return "" — the CLI will write a stub session.
 */
export function materializeTranscript(event, { tmpDir = tmpdir() } = {}) {
  const raw = event?.transcriptPath || event?.transcript_path;
  if (typeof raw === "string" && raw.length > 0) return raw;

  const messages =
    event?.messages || event?.turns || event?.conversation || event?.history || null;
  if (!Array.isArray(messages) || messages.length === 0) return "";

  const normalized = messages.map(normalizeMessage).filter(Boolean);
  if (normalized.length === 0) return "";

  const sid = event?.sessionId || event?.session_id || "openclaw";
  const safeSid = String(sid).replace(/[^A-Za-z0-9_-]/g, "_");
  const path = join(tmpDir, `openclaw-${safeSid}-${Date.now()}.jsonl`);
  writeFileSync(path, normalized.map((l) => JSON.stringify(l)).join("\n"));
  return path;
}

/**
 * Translate an OpenClaw agent_end event into the JSON payload that
 * `memoryd capture` reads on stdin. Tolerant of missing fields — the
 * CLI handles partial payloads (plan 1 behavior).
 */
export function buildPayload(event, opts) {
  const sessionId =
    event?.sessionId || event?.session_id || event?.threadId || "openclaw-unknown";
  const cwd = event?.cwd || event?.workspace?.cwd || process.cwd();
  const transcriptPath = materializeTranscript(event, opts);

  return {
    session_id: sessionId,
    transcript_path: transcriptPath,
    cwd,
  };
}

export function spawnCapture(payload, { bin = DEFAULT_MEMORYD_BIN, logFile } = {}) {
  const logPath = logFile || logFor();
  const child = spawn(bin, ["capture", "--source", "openclaw"], {
    stdio: ["pipe", "pipe", "pipe"],
    detached: true,
  });
  child.stdout.on("data", (chunk) => appendFileSync(logPath, chunk));
  child.stderr.on("data", (chunk) => appendFileSync(logPath, chunk));
  child.on("close", (code) => {
    appendFileSync(logPath, tsLine(code === 0 ? "ok" : `failed (exit ${code})`));
  });
  child.on("error", (err) => {
    appendFileSync(logPath, tsLine(`spawn error: ${err.message}`));
  });
  child.stdin.write(JSON.stringify(payload));
  child.stdin.end();
  child.unref();
  return child;
}

/**
 * OpenClaw plugin entry. The SDK injects `api`; we call `api.on('agent_end', ...)`.
 * See https://github.com/openclaw/openclaw/blob/main/docs/plugins/sdk-overview.md
 */
export default function register(api) {
  api.on("agent_end", async (event) => {
    try {
      const payload = buildPayload(event);
      spawnCapture(payload);
    } catch (err) {
      // Never let plugin errors crash OpenClaw.
      try {
        appendFileSync(logFor(), tsLine(`handler error: ${err.message}`));
      } catch (_) {
        // give up silently
      }
    }
  });
}
