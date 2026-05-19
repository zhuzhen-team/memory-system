/**
 * Pure helpers shared between SDK entry (index.js) and tests (payload.test.mjs).
 * No OpenClaw SDK imports; safe to import directly in node --test.
 */

import { spawn } from "node:child_process";
import { mkdirSync, appendFileSync, existsSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

// Resolve memoryd CLI: MEMORYD_BIN env var wins, else fall back to the
// dev venv path (the path used during local development on the original
// author's machine; LaunchAgent + Phase 1 setup CLI should always set
// MEMORYD_BIN explicitly).
export const DEFAULT_MEMORYD_BIN =
  process.env.MEMORYD_BIN ||
  "/Users/abble/memory-system/memoryd/.venv/bin/memoryd";

export function logFor() {
  const dataRoot =
    process.env.MEMORYD_DATA_ROOT || join(homedir(), ".local", "share", "memoryd");
  const logDir = join(dataRoot, "logs");
  if (!existsSync(logDir)) mkdirSync(logDir, { recursive: true });
  return join(logDir, "openclaw-events.log");
}

export function tsLine(extra) {
  return `${new Date().toISOString()}  ${extra}\n`;
}

export function normalizeMessage(m) {
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

  return { type, message: { content: [{ type: "text", text }] } };
}

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
