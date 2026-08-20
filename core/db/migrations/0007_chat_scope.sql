-- A chat's knowledge scope becomes many-to-many (any subset of ingested channels), and its
-- voice (Neutral or one selected creator) becomes independent of that scope.
CREATE TABLE chat_sources (
    chat_id UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    position SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, channel_id)
);
CREATE INDEX idx_chat_sources_channel_id ON chat_sources(channel_id);

-- NULL = Neutral voice. ON DELETE SET NULL: deleting the voice's channel silently falls back
-- to Neutral rather than deleting the chat.
ALTER TABLE chats ADD COLUMN voice_channel_id UUID REFERENCES channels(id) ON DELETE SET NULL;

-- Backfill every existing chat into the new shape before the old column is dropped.
INSERT INTO chat_sources (chat_id, channel_id, position)
SELECT id, channel_id, 0 FROM chats;

UPDATE chats c SET voice_channel_id = c.channel_id
FROM channels ch
WHERE ch.id = c.channel_id
  -- Persona didn't exist before this phase, so branding has no "persona" key for any existing
  -- channel — absent key means "enabled" (Part B's default), matching today's single-channel
  -- chat feel: a chat's one channel was always its de-facto voice.
  AND COALESCE((ch.branding->'persona'->>'enabled')::boolean, true);

DROP INDEX IF EXISTS idx_chats_channel_id;
ALTER TABLE chats DROP COLUMN channel_id;

-- Full scope per message, alongside the existing single channel_id (kept as voice-else-first-
-- source for backward-compat reporting). Stored as text uuids in jsonb, not a join table, so
-- the record survives channel deletion the same way usage_events.channel_id already does
-- (ON DELETE SET NULL) — a metering/rev-share record must outlive the channels it references.
ALTER TABLE usage_events ADD COLUMN source_channel_ids JSONB NOT NULL DEFAULT '[]';
UPDATE usage_events SET source_channel_ids = jsonb_build_array(channel_id::text)
WHERE channel_id IS NOT NULL;
