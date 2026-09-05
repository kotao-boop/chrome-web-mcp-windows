# chrome-web-mcp: everything bundled, user needs only Docker.
#   docker build -t chrome-web-mcp .
#   echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' \
#     | docker run -i --rm chrome-web-mcp
#
# MCP client config (stdio over docker):
#   { "command": "docker", "args": ["run", "-i", "--rm", "chrome-web-mcp"] }
#
# Visible-window mode needs the host X socket (Linux with X11):
#   docker run -i --rm -e DISPLAY=$DISPLAY -e CW_DISPLAY_MODE=xephyr \
#     -v /tmp/.X11-unix:/tmp/.X11-unix chrome-web-mcp
ARG DEBIAN_FRONTEND=noninteractive

FROM debian:bookworm-slim AS base
ARG DEBIAN_FRONTEND
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      chromium xvfb xserver-xephyr xdotool wmctrl x11-utils \
      fonts-liberation fonts-noto-cjk \
      sqlite3 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f > /dev/null

FROM base AS app
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir .
ENV PATH="/opt/venv/bin:${PATH}" \
    CW_DISPLAY_MODE="xvfb"
ENTRYPOINT ["chrome-web-mcp"]
