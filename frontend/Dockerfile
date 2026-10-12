FROM node:24-alpine AS deps
WORKDIR /app
COPY package*.json ./
RUN npm ci

FROM node:24-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Must be in the BUILDER stage: Next inlines NEXT_PUBLIC_* at build time, so
# setting this in the runner stage would compile to undefined.
ARG APP_VERSION=dev
ENV NEXT_PUBLIC_APP_VERSION=${APP_VERSION}
# Next.js sends anonymous usage telemetry to Vercel by default; PRIVACY.md says
# this project has it off, in the build and (below) at runtime.
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
# The same server the uv install runs: Next's standalone output, started with
# `node server.js` (no node_modules or package.json copy; the bundle carries
# the few packages it traces). A container must listen on all interfaces or
# the published port cannot reach it; docker-compose.yml binds only the HOST
# side to 127.0.0.1, so nothing is exposed beyond loopback.
ENV HOSTNAME=0.0.0.0 PORT=3000
# `node` (uid 1000) ships with the base image. Running the server as root buys
# nothing: it serves a prebuilt bundle and owns no persistent state, and costs
# the usual root-in-container blast radius. --chown so the server can still
# write its own cache under .next/.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
