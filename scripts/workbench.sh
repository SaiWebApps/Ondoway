#!/usr/bin/env bash
# Run the editorial workbench: start the graph API on this lane's DEV graph,
# wait for /api/v1/healthz, then open frontend/review.html pointed at it.
#
# review.html reads ?apiPort= (default 8000) and builds
# http://localhost:<port>/api/v1 — so a plain file:// open works. We open it
# with the lane's port explicitly.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# The lane's own API port (`make workbench` exports it; 8000 on the main lane).
# Per-lane ports are what keep two sandboxes' workbenches blind to each other:
# a reuse probe on this port can only ever find this lane's server.
PORT="${ONDOWAY_API_PORT:-8000}"
HEALTH="http://localhost:${PORT}/api/v1/healthz"
PAGE="file://${ROOT}/frontend/review.html?apiPort=${PORT}"
LOG="/tmp/ondoway-workbench-api-${PORT}.log"

# Same NO_PROXY prefix as `make api` — hard-won fix, keep in sync.
NP="api.resend.com,resend.com,www.googleapis.com,googleapis.com,api.anthropic.com,anthropic.com,api.github.com,github.com"

echo "==> Workbench: starting graph API on the DEV graph (port ${PORT})..."

# If something already answers healthz on this lane's port, reuse it. Otherwise start one.
if curl -fs --max-time 2 "$HEALTH" >/dev/null 2>&1; then
  echo "    API already healthy on :${PORT} — reusing it."
  API_PID=""
else
  # Free the port in case a stale uvicorn is lingering (matches make flutter-ios).
  # LISTEN-scoped only: bare `lsof -i:PORT` also matches CLIENT sockets
  # (ESTABLISHED/CLOSED/TIME_WAIT) belonging to unrelated processes merely
  # talking to this port from the other side (measured: the user's Claude
  # desktop app held two CLOSED client sockets to :8000 and was getting
  # killed by this line). Only a LISTEN-state socket is a stale server.
  PORT_KILL_PIDS=$(lsof -tiTCP:${PORT} -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$PORT_KILL_PIDS" ]; then
    for pid in $PORT_KILL_PIDS; do
      cmd=$(ps -p "$pid" -o comm= 2>/dev/null | tail -1)
      echo "    Freeing :${PORT} — killing stale listener PID ${pid} (${cmd:-unknown})"
    done
    kill $PORT_KILL_PIDS 2>/dev/null || true
  fi
  cd "$ROOT"
  # The auth signing-secret guard (src/api/auth/config.py) fails closed on an
  # empty/short secret. A direct `bash scripts/workbench.sh` (not via `make
  # workbench`, which exports this) has no real secret and no pytest, so opt
  # into the dev placeholder — a local workbench server is never production.
  # WORKBENCH_API_ENABLED=true: the graph-CRUD gate is now fail-closed (app.py),
  # so the workbench routers this page needs must be opted in explicitly.
  # (ONDOWAY_ALLOW_DIRTY_LOCAL_BUILD used to be set here. A dirty local tree is
  # no longer a refusal anywhere — resolve_build_identity() stamps HEAD tagged
  # dirty and warns, with no flag to remember — so the line is gone, 2026-08-18.)
  # TTS_PROVIDER=openai: OWNER RULING 2026-07-31 — the workbench must resolve the
  # SAME real implementations production resolves. Without this line the server
  # inherited get_provider()'s "mock" fallback (src/audio/provider.py), so any
  # /audio request that omitted an explicit provider was answered with a SILENT
  # WAV that an editor could mistake for real narration. render.yaml pins
  # TTS_PROVIDER=openai for production; this keeps the two in parity, which
  # tests/test_workbench_matches_the_app.py now verifies by derivation.
  # ONBOARD_PROVIDER=anthropic: OWNER DECISION 2026-07-31 — the same rule, applied
  # to the editorial side. get_drafter() (src/onboard/beat_draft.py) used to return
  # the free MockBeatDrafter for an unset value, and this script pinned nothing, so
  # the workbench's Draft Beats button showed a human beats a fake had written. The
  # drafter now fails closed, and this is the pin that makes the button real. It
  # SPENDS: one Opus call per drafted beat, and the owner removed the cost dialog
  # (frontend/onboard.html), so a click drafts a whole city's beats immediately.
  # (Rate limiters used to be disabled here with five env vars. The limiters
  # themselves were DELETED from src/api/routes/{trips,audio}.py on 2026-07-31,
  # so there is nothing left to switch off.)
  # TTS_FALLBACK: the SAME rule again. render.yaml gives production an understudy
  # for the tourist's audio, so the workbench must resolve the same chain —
  # otherwise an editor judging a tour during an OpenAI outage sees the stop
  # simply fail while the app would have voiced it, and the two surfaces are no
  # longer the same construction site. Only the unnamed path chains, so the
  # page's own provider dropdown still previews exactly the voice it names.
  # The THIRD tier is not a server provider at all — it is this page's own
  # browser voice, the workbench twin of the phone's, spoken from the stop's
  # text when no audio file exists. Neither surface names it here.
  WORKBENCH_API_ENABLED=true \
    TTS_PROVIDER=openai \
    TTS_FALLBACK=elevenlabs \
    ONBOARD_PROVIDER=anthropic \
    NO_PROXY="$NP" no_proxy="$NP" ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS=1 \
    uv run uvicorn src.api.app:app --host 127.0.0.1 --port ${PORT} >"${LOG}" 2>&1 &
  API_PID=$!
  echo "    API PID ${API_PID} (log: ${LOG})"
fi

echo "==> Waiting for ${HEALTH} ..."
ok=0
for i in $(seq 1 30); do
  if curl -fs --max-time 2 "$HEALTH" >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done

if [ "$ok" -ne 1 ]; then
  echo "ERROR: API did not become healthy on :${PORT} within 30s." >&2
  echo "       Check the log: ${LOG}" >&2
  echo "       Common cause: dev Neo4j not up (make db-up) or port ${PORT} busy." >&2
  exit 1
fi
echo "    API healthy."

echo "==> Opening the workbench: ${PAGE}"
if command -v open >/dev/null 2>&1; then
  open "$PAGE"                       # macOS
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$PAGE"                   # Linux
else
  echo "    Open this URL manually: ${PAGE}"
fi

echo ""
echo "Workbench is live. The API is on http://localhost:${PORT} (this lane's dev graph)."
if [ -n "$API_PID" ]; then
  echo "Stop the API when done:  kill ${API_PID}   (or: lsof -tiTCP:${PORT} -sTCP:LISTEN | xargs kill)"
fi
