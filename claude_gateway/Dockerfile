FROM node:20-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python3 -m pip install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY mcp_server.py /app/mcp_server.py
COPY docs/mock_data_docs.md /app/mock_data_docs.md
COPY claude_gateway/gateway_server.py /app/gateway_server.py
COPY claude_gateway/render_config.py /app/render_config.py
COPY claude_gateway/role_prompt.md /app/claude_gateway/role_prompt.md
COPY claude_gateway/mcp_servers.json.template /app/mcp_servers.json.template

# Reuse the existing schema/business-rules doc as part of the system prompt
# instead of duplicating it.
RUN cat /app/claude_gateway/role_prompt.md /app/mock_data_docs.md > /app/system_prompt.md

# node:20-slim already ships a "node" user (uid 1000) - reuse it instead of
# creating a new one, since useradd -u 1000 would collide with it.
RUN mkdir -p /home/node/.claude && chown -R node:node /home/node /app
USER node

EXPOSE 8090
CMD ["python3", "gateway_server.py"]
