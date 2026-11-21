CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  tool_call_id TEXT NOT NULL REFERENCES tasks(tool_call_id),
  kind TEXT NOT NULL CHECK (kind IN ('approval', 'result', 'failure')),
  request_id TEXT NOT NULL DEFAULT '',
  danger TEXT CHECK (danger IN ('low', 'medium', 'high')),
  state TEXT NOT NULL CHECK (state IN ('arrived', 'mentioned', 'essence', 'closed')),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  closed_reason TEXT,
  UNIQUE (tool_call_id, kind, request_id)
) STRICT;

CREATE INDEX events_open ON events(created_at) WHERE state != 'closed';

INSERT INTO events (tool_call_id, kind, state, created_at, updated_at, closed_reason)
SELECT tool_call_id,
       CASE status WHEN 'succeeded' THEN 'result' ELSE 'failure' END,
       CASE WHEN delivered_at IS NULL THEN 'arrived' ELSE 'closed' END,
       created_at,
       COALESCE(delivered_at, created_at),
       CASE
         WHEN delivered_at IS NULL THEN NULL
         WHEN stop_reason = 'user' THEN 'cancelled'
         ELSE 'delivered'
       END
FROM tasks WHERE status IN ('succeeded', 'failed');

INSERT INTO events (tool_call_id, kind, request_id, danger, state, created_at, updated_at)
SELECT tool_call_id, 'approval',
       COALESCE(CAST(json_extract(approval, '$.request_id') AS TEXT), ''),
       'high', 'arrived', created_at, created_at
FROM tasks WHERE status = 'waiting_approval' AND approval IS NOT NULL;
