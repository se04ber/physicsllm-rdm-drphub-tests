#!/bin/sh
# Start the MCP server on loopback, wait for it, then measure against it.
#
# This lives in a file rather than inline in reana.yaml because REANA
# substitutes ${...} in step commands, so a shell loop using $(...) or $i is
# parsed as a workflow placeholder and the run dies with "Invalid placeholder
# in string" before anything executes.
set -eu
PORT="${1:-8000}"
SUITE="${2:-benchmark/TestBenchmark_PaN}"

mkdir -p results

# ALLOWED_HOSTS must name loopback explicitly, bare and with the port: the
# server validates Host headers against DNS rebinding and does not exempt
# 127.0.0.1.
RDM_MCP_ENV=production \
RDM_MCP_HOST=127.0.0.1 \
RDM_MCP_PORT="$PORT" \
RDM_MCP_ALLOWED_HOSTS="127.0.0.1,127.0.0.1:$PORT,localhost,localhost:$PORT" \
  rdm-mcp > results/server.log 2>&1 &
SERVER_PID=$!

# Wait for the port rather than sleeping a guess: an arbitrary sleep is either
# too short on a slow node or wasted time on a fast one.
i=0
while [ "$i" -lt 30 ]; do
  if python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1', $PORT))==0 else 1)"; then
    echo "server listening on 127.0.0.1:$PORT after ${i}s"
    break
  fi
  i=$((i + 2))
  sleep 2
done

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "server exited before it listened; its log follows" >&2
  cat results/server.log >&2 || true
fi

python3 run_card.py --tree "$SUITE" --base "http://127.0.0.1:$PORT" \
  --tls verify --out results --write-results
