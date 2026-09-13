#!/bin/bash
# Render start: PO-token sidecar + API server in one service.
set -e
if [ -x ./bgutil-pot ]; then
  ./bgutil-pot server --port 4416 &
  POT_PID=$!
  trap "kill $POT_PID" EXIT
fi
exec uvicorn main:app --host 0.0.0.0 --port $PORT
