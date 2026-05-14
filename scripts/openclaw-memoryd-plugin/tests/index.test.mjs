import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildPayload, materializeTranscript } from "../src/index.mjs";

test("buildPayload extracts canonical fields when transcriptPath given", () => {
  const ev = {
    sessionId: "ow-123",
    cwd: "/Users/abble/projects/wolin",
    transcriptPath: "/tmp/ow.jsonl",
  };
  const p = buildPayload(ev);
  assert.equal(p.session_id, "ow-123");
  assert.equal(p.cwd, "/Users/abble/projects/wolin");
  assert.equal(p.transcript_path, "/tmp/ow.jsonl");
});

test("buildPayload falls back to snake_case keys", () => {
  const ev = {
    session_id: "ow-snake",
    workspace: { cwd: "/tmp/snake-proj" },
    transcript_path: "",
  };
  const p = buildPayload(ev);
  assert.equal(p.session_id, "ow-snake");
  assert.equal(p.cwd, "/tmp/snake-proj");
  assert.equal(p.transcript_path, "");
});

test("buildPayload returns empty transcript_path on missing fields", () => {
  const p = buildPayload({});
  assert.equal(p.session_id, "openclaw-unknown");
  assert.ok(typeof p.cwd === "string");
  assert.equal(p.transcript_path, "");
});

test("buildPayload tolerates null event", () => {
  const p = buildPayload(null);
  assert.equal(p.session_id, "openclaw-unknown");
});

test("materializeTranscript writes inline messages as CC-format JSONL", () => {
  const dir = mkdtempSync(join(tmpdir(), "ow-test-"));
  const ev = {
    sessionId: "ow-inline",
    messages: [
      { role: "user", content: "你好" },
      { role: "assistant", content: [{ type: "text", text: "hi back" }] },
      { author: "user", text: "再问一句" },
    ],
  };
  const path = materializeTranscript(ev, { tmpDir: dir });
  assert.ok(existsSync(path));
  const lines = readFileSync(path, "utf-8").trim().split("\n");
  assert.equal(lines.length, 3);
  const first = JSON.parse(lines[0]);
  assert.equal(first.type, "user");
  assert.equal(first.message.content[0].text, "你好");
  const second = JSON.parse(lines[1]);
  assert.equal(second.type, "assistant");
  assert.equal(second.message.content[0].text, "hi back");
  const third = JSON.parse(lines[2]);
  assert.equal(third.type, "user");
  assert.equal(third.message.content[0].text, "再问一句");
});

test("materializeTranscript returns empty string when no messages and no path", () => {
  const dir = mkdtempSync(join(tmpdir(), "ow-test-"));
  assert.equal(materializeTranscript({}, { tmpDir: dir }), "");
  assert.equal(materializeTranscript({ messages: [] }, { tmpDir: dir }), "");
  assert.equal(materializeTranscript(null, { tmpDir: dir }), "");
});

test("materializeTranscript prefers transcriptPath over inline messages", () => {
  const dir = mkdtempSync(join(tmpdir(), "ow-test-"));
  const ev = {
    transcriptPath: "/preset/path.jsonl",
    messages: [{ role: "user", content: "x" }],
  };
  assert.equal(materializeTranscript(ev, { tmpDir: dir }), "/preset/path.jsonl");
});
