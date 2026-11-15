FROM node:22-alpine

WORKDIR /app

RUN npm init -y \
    && npm install --omit=dev --save-exact @spatialy/huly-mcp@0.1.54

ENV MCP_TRANSPORT=http
ENV MCP_HTTP_HOST=0.0.0.0
ENV MCP_HTTP_PORT=3002
ENV TOOLSETS=issues,attachments
ENV MCP_AUTO_EXIT=false

EXPOSE 3002

CMD ["node", "node_modules/@spatialy/huly-mcp/dist/index.cjs"]
