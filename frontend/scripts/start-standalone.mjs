// `npm start`: run the production build the way Docker and the uv install run it.
//
// `output: "standalone"` makes `next start` the wrong server (Next warns). The standalone
// bundle is `.next/standalone/server.js`; it expects `.next/static` and `public` beside it,
// which the build leaves elsewhere. This copies them in and starts that one file.
//
// Listens on 127.0.0.1 only (SYSTEM.md section 6, inv-browser-boundary). The shell's own
// HOSTNAME variable is a machine name, so it is ignored. PORT is honoured (default 3000).
// Next's anonymous telemetry stays on unless you set NEXT_TELEMETRY_DISABLED=1 (PRIVACY.md).

import { spawn } from "node:child_process";
import { cpSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const standalone = resolve(root, ".next", "standalone");
const server = resolve(standalone, "server.js");

if (!existsSync(server)) {
  console.error("There is no production build yet. Run `npm run build` first.");
  process.exit(1);
}

cpSync(resolve(root, ".next", "static"), resolve(standalone, ".next", "static"), { recursive: true });
cpSync(resolve(root, "public"), resolve(standalone, "public"), { recursive: true });

const child = spawn(process.execPath, [server], {
  cwd: standalone,
  stdio: "inherit",
  env: { ...process.env, HOSTNAME: "127.0.0.1", NODE_ENV: "production" },
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}
child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
