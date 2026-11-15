FROM node:22-alpine

WORKDIR /app

RUN npm init -y \
    && npm install --omit=dev --save-exact @bgx4k3p/huly-mcp-server@2.2.4

ENV PORT=3001
EXPOSE 3001

CMD ["node", "node_modules/@bgx4k3p/huly-mcp-server/src/server.mjs"]
