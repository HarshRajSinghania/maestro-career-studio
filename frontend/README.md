# Maestro CS frontend

The Next.js (App Router, TypeScript) web app. It talks to the backend through
its own same-origin `/api` proxy.

```bash
npm ci
API_PROXY_BACKEND=http://127.0.0.1:8001 npm run dev   # backend default; omit if unchanged
```

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the full dev setup and
[`docs/frontend-conventions.md`](../docs/frontend-conventions.md) for layout,
accessibility, naming and copy rules.
