# ---- Dependencies (reproducible install from the lockfile) ----
FROM node:22-slim AS deps
WORKDIR /app
# Copy the lockfile before installing so `npm ci` installs the exact,
# reproducible dependency tree.
COPY package.json package-lock.json ./
RUN npm ci

# ---- Build ----
FROM node:22-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---- Runtime (standalone: only the files needed to run) ----
FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# Run as the non-root user shipped with the node image.
USER node
# The standalone output bundles a minimal server.js plus only the runtime
# node_modules it actually needs. Static assets are copied separately.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
