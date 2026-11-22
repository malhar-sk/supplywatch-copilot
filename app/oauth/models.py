from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class OAuthClient(Base):
    """OAuth2 client-credentials client, owned by an API key (its company).
    Only a SHA-256 hash of the secret is stored (secrets are 256-bit random)."""

    __tablename__ = "oauth_clients"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    secret_hash: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id"), index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
