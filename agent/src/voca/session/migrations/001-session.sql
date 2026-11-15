CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  started_at REAL NOT NULL,
  ended_at REAL,
  end_reason TEXT,
  context_json TEXT,
  context_at REAL,
  compacted_at REAL,
  summary_count INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  at REAL NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('user', 'assistant', 'tool_call', 'tool_result', 'summary')),
  item_id TEXT NOT NULL,
  call_id TEXT,
  name TEXT,
  text TEXT NOT NULL,
  data_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (session_id, item_id)
) STRICT;

CREATE INDEX messages_session_at ON messages(session_id, at);

-- The index holds the text with ё/Ё folded to е/Е; messages.text keeps ё.
-- FTS 'rebuild' would index the unfolded text, so rebuild through the same expression:
--   INSERT INTO messages_fts(messages_fts) VALUES ('delete-all');
--   INSERT INTO messages_fts(rowid, text) SELECT id, replace(replace(text, 'ё', 'е'), 'Ё', 'Е') FROM messages;
CREATE VIRTUAL TABLE messages_fts USING fts5(text, content='messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2');

CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, text) VALUES (new.id, replace(replace(new.text, 'ё', 'е'), 'Ё', 'Е'));
END;
CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, replace(replace(old.text, 'ё', 'е'), 'Ё', 'Е'));
END;
CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, replace(replace(old.text, 'ё', 'е'), 'Ё', 'Е'));
  INSERT INTO messages_fts(rowid, text) VALUES (new.id, replace(replace(new.text, 'ё', 'е'), 'Ё', 'Е'));
END;
