// lms-control: Matthew's fixed /lms commands from WhatsApp and Telegram (D-080).
//
// This file holds no logic. Every command is decided and carried out by
// ops/remote.py, which checks the sender a second time, refuses anything not
// on its list, writes the audit row and prints the reply. Plugin commands run
// before the agent, so the model never sees or chooses an action (D-056).
//
// execFile, never a shell: the words Matthew typed are arguments, not code.

import { execFile } from "node:child_process";

const LMS = "/Users/mleca/lms-repo/lms";
const PYTHON = `${LMS}/.venv/bin/python`;
const SCRIPT = "/Users/mleca/lms-repo/lms/ops/remote.py";
const CHANNELS = new Set(["telegram", "whatsapp"]);
const MAX_WORDS = 4;
// /halt is not a built-in on 2026.7.1-2 (Mac facts row 5). Set false if a
// later OpenClaw reserves it: registration would fail and take /lms with it.
const REGISTER_HALT = true;

function words(raw) {
  return String(raw ?? "").trim().split(/\s+/).filter(Boolean).slice(0, MAX_WORDS);
}

function runRemote(channel, sender, argWords) {
  return new Promise((resolve) => {
    execFile(
      PYTHON,
      [SCRIPT, "--channel", channel, "--sender", String(sender ?? ""), "--", ...argWords],
      { cwd: LMS, timeout: 60_000, maxBuffer: 64 * 1024, shell: false },
      (err, stdout) => {
        const text = String(stdout ?? "").trim();
        if (err && err.killed) return resolve("Still running. See ~/LMS/logs/remote.log.");
        if (err && !text) return resolve("Failed. See ~/LMS/logs/remote.log.");
        resolve(text);
      },
    );
  });
}

// Nothing is sent back: a stranger, another channel, or remote.py printing
// nothing. suppressReply stops OpenClaw's fallback reply (Mac facts row 4).
const SILENT = { suppressReply: true };

async function handle(ctx, argWords) {
  const channel = ctx.channel;
  if (!CHANNELS.has(channel)) return SILENT;
  // OpenClaw says this sender may not run commands: no reply, no process.
  if (ctx.isAuthorizedSender === false) return SILENT;
  const text = await runRemote(channel, ctx.senderId ?? ctx.from, argWords);
  return text ? { text } : SILENT;
}

// The shape definePluginEntry() returns (shipped docs/plugins/*.md), written
// out so the plugin has no import from OpenClaw's own install: a plugin
// outside node_modules/openclaw may not resolve "openclaw/plugin-sdk/...".
export default {
  id: "lms-control",
  name: "LMS control",
  description: "Matthew's fixed /lms commands (D-080). Runs ops/remote.py; no AI involved.",
  register,
};

function register(api) {
  api.registerCommand({
    name: "lms",
    description: "Matthew's LMS commands (/lms help)",
    acceptsArgs: true,
    requireAuth: true,
    handler: (ctx) => handle(ctx, words(ctx.args)),
  });

  if (REGISTER_HALT) {
    api.registerCommand({
      name: "halt",
      description: "Stop every LMS and OpenClaw job now",
      acceptsArgs: false,
      requireAuth: true,
      handler: (ctx) => handle(ctx, ["halt"]),
    });
  }
}
