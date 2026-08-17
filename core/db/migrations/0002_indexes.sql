CREATE INDEX idx_videos_channel_id ON videos(channel_id);
CREATE INDEX idx_videos_status ON videos(status);
CREATE INDEX idx_chunks_video_id ON chunks(video_id);
CREATE INDEX idx_chunks_channel_id ON chunks(channel_id);
CREATE INDEX idx_ingest_jobs_channel_id ON ingest_jobs(channel_id);
CREATE INDEX idx_ingest_jobs_status ON ingest_jobs(status);
CREATE INDEX idx_messages_chat_id ON messages(chat_id);

CREATE INDEX idx_chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops);
