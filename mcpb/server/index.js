#!/usr/bin/env node
// Claude Desktop stdio ↔ Maestro's HTTP MCP endpoint. Node ≥ 18; no dependencies.
"use strict";

const readline = require("node:readline");

// Unfilled user_config values can arrive as literal placeholders.
const setting = (name) => {
  const raw = (process.env[name] || "").trim();
  return /^\$\{.*\}$/.test(raw) ? "" : raw;
};

const mcpUrl = (
  backendUrl = setting("MAESTRO_CS_BACKEND_URL") || "http://localhost:8001",
  profile = setting("MAESTRO_CS_MCP_PROFILE") || "full",
) => `${backendUrl.replace(/\/+$/, "")}/mcp${profile === "full" ? "" : `/${profile}`}`;

async function main() {
  const backendUrl = setting("MAESTRO_CS_BACKEND_URL") || "http://localhost:8001";
  const url = mcpUrl(backendUrl);
  let sessionId;
  let initializeLine;
  let initializedLine;

  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => process.exit(0));
  }

  const write = (body) => process.stdout.write(JSON.stringify(body) + "\n");
  const fail = (request, message) => {
    const requests = Array.isArray(request) ? request : [request];
    const errors = requests.filter((item) => item && Object.hasOwn(item, "id"))
      .map(({ id }) => ({ jsonrpc: "2.0", id, error: { code: -32000, message } }));
    if (errors.length) write(Array.isArray(request) ? errors : errors[0]);
  };

  async function post(line) {
    const headers = {
      Accept: "application/json, text/event-stream",
      "Content-Type": "application/json",
    };
    if (sessionId) headers["Mcp-Session-Id"] = sessionId;
    let response;
    let text;
    try {
      response = await fetch(url, { method: "POST", headers, body: line });
      text = await response.text();
    } catch {
      const message = `Maestro Career Studio is not running at ${backendUrl}. Start it (docker compose up -d, or open the app) and try again.`;
      process.stderr.write(message + "\n");
      throw new Error(message);
    }
    let body;
    if (response.status === 200) {
      try { body = JSON.parse(text); }
      catch { throw new Error("Maestro Career Studio returned an invalid JSON response."); }
    }
    return { status: response.status, sessionId: response.headers.get("mcp-session-id"), body };
  }

  function accepted(response) {
    if (response.status !== 200 && response.status !== 202) {
      throw new Error(`Maestro Career Studio returned HTTP ${response.status}.`);
    }
    return response;
  }

  const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity, terminal: false });
  // Await each complete response, including a session replay, before reading on.
  for await (const line of lines) {
    if (!line.trim()) continue;
    let request;
    try { request = JSON.parse(line); }
    catch {
      fail({ id: null }, "The MCP request is not valid JSON.");
      continue;
    }
    if (request.method === "initialize") initializeLine = line;
    if (request.method === "notifications/initialized") initializedLine = line;
    try {
      let response = await post(line);
      if (response.status === 404 && sessionId && initializeLine) {
        // A restarted backend must see a fresh handshake without the stale id.
        sessionId = undefined;
        const initialized = accepted(await post(initializeLine));
        sessionId = initialized.sessionId;
        if (initializedLine) accepted(await post(initializedLine));
        response = await post(line);
      }
      accepted(response);
      if (request.method === "initialize") sessionId = response.sessionId;
      if (response.status === 200) write(response.body);
    } catch (error) {
      fail(request, error.message);
    }
  }
}

module.exports = { mcpUrl, setting, main };
if (require.main === module) main();
