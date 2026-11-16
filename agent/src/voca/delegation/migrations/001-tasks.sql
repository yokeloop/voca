CREATE TABLE tasks (
  tool_call_id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'waiting_approval', 'unknown', 'succeeded', 'failed')),
  run_id TEXT,
  result TEXT,
  error TEXT,
  created_at REAL NOT NULL,
  delivered_at REAL,
  stop_requested_at REAL,
  approval TEXT
) STRICT;

CREATE INDEX tasks_open ON tasks(created_at) WHERE status IN ('queued', 'running', 'waiting_approval', 'unknown');
