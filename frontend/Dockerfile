# Dashboard image. Stage 1 builds the static bundle; stage 2 serves it with `vite preview`,
# which also proxies /api, /health and /metrics to the backend (VITE_API_PROXY).
# For production, serve dist/ from a CDN or reverse proxy instead (see docs/DECISIONS.md).
ARG NODE_IMAGE=node:22-alpine
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM ${NODE_IMAGE}
WORKDIR /app
ENV NODE_ENV=production \
    VITE_API_PROXY=http://backend:8000
COPY --from=build /app/package.json /app/package-lock.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=build /app/vite.config.ts ./vite.config.ts
# Run unprivileged. Vite writes its bundled config next to vite.config.ts and under
# node_modules/.vite-temp, so those directories must be writable by the runtime user.
RUN (id -u node >/dev/null 2>&1 || useradd --system --create-home node 2>/dev/null || adduser -D node) \
    && mkdir -p node_modules/.vite-temp && chown node /app /app/node_modules /app/node_modules/.vite-temp
USER node
EXPOSE 8080
CMD ["npx", "vite", "preview", "--host", "0.0.0.0", "--port", "8080", "--strictPort"]
