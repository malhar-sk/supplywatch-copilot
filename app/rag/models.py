"""RAG tables. Same declarative Base as the source app, so create_all() makes
them too. Requires the pgvector extension (db/init.sql creates it)."""
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, Computed, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from core.config import get_settings
from core.database import Base

EMBED_DIM = get_settings().embedding_dim


class Document(Base):
    __tablename__ = "rag_documents"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    title: Mapped[str] = mapped_column(String(400))
    source: Mapped[str] = mapped_column(String(400), default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[str] = mapped_column(String(32), default="")
    material: Mapped[str] = mapped_column(String(200), default="")
    doc_type: Mapped[str] = mapped_column(String(60), default="")
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Chunk(Base):
    __tablename__ = "rag_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("rag_documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    n_tokens: Mapped[int] = mapped_column(Integer, default=0)
    embedding = mapped_column(Vector(EMBED_DIM))
    tsv = mapped_column(TSVECTOR, Computed("to_tsvector('english', text)", persisted=True))

    __table_args__ = (
        Index("ix_rag_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_rag_chunks_embedding_hnsw", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )
