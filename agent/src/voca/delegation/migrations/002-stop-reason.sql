ALTER TABLE tasks ADD COLUMN stop_reason TEXT CHECK (stop_reason IN ('user', 'timeout'));
UPDATE tasks SET stop_reason = 'timeout' WHERE stop_requested_at IS NOT NULL;
