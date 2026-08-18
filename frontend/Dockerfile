FROM node:22.23.2-alpine AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22.23.2-alpine AS builder
WORKDIR /app
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=${BACKEND_URL}
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
RUN npm run check:ocr-assets
RUN npm run build

FROM node:22.23.2-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
