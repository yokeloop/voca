#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/env.sh"

page="${1:-http://$VOCA_WEB_HOST:$VOCA_WEB_PORT}"
python="$VOCA_ROOT/agent/.venv/bin/python"
if [ ! -x "$python" ]; then
  echo "нет agent/.venv/bin/python: (cd agent && uv sync --locked)" >&2
  exit 1
fi
client_version="$(jq -r '.dependencies["livekit-client"]' "$VOCA_ROOT/web/package.json")"

response="$(curl -fsS -X POST -H 'Content-Type: application/json' -d '{"device":"check"}' "$page/api/token")"
token="$(jq -r .token <<<"$response")"
livekit="$(jq -r .url <<<"$response")"
base="${livekit/#wss:/https:}"
base="${base/#ws:/http:}"

join_request="$("$python" - "$client_version" <<'EOF'
import base64
import gzip
import sys

from livekit.protocol import models, rtc

info = models.ClientInfo(
    sdk=models.ClientInfo.JS, version=sys.argv[1], protocol=17, client_protocol=2
)
join = rtc.JoinRequest(
    client_info=info, connection_settings=rtc.ConnectionSettings(auto_subscribe=True)
)
wrapped = rtc.WrappedJoinRequest(
    join_request=gzip.compress(join.SerializeToString()),
    compression=rtc.WrappedJoinRequest.GZIP,
)
print(base64.urlsafe_b64encode(wrapped.SerializeToString()).decode())
EOF
)"

failed=0
for url in "$base/rtc/validate?access_token=$token" "$base/rtc/v1/validate?access_token=$token&join_request=$join_request"; do
  code="$(curl -s -o /dev/null -w '%{http_code}' "$url" || true)"
  echo "$code ${url%%\?*}"
  [ "$code" = 200 ] || failed=1
done
exit "$failed"
