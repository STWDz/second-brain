from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Telegram
    bot_token: str
    webapp_url: str = ""

    # ── Security ──
    # Comma-separated list of allowed Telegram user IDs (empty = everyone allowed)
    allowed_users: str = ""
    # Comma-separated list of admin Telegram user IDs (can use /admin commands)
    admin_ids: str = ""
    # Only allow private chats (block groups/channels)
    private_only: bool = True
    # Max text input length (chars) to prevent abuse
    max_text_length: int = 50000
    # Max URL fetch requests per user per hour
    max_url_per_hour: int = 20
    # Max extracted content length (chars) per single source — protects Groq/Whisper quota
    max_content_chars: int = 120000
    # Max YouTube video duration in minutes (0 = unlimited)
    max_youtube_minutes: int = 90

    # ── Webhook mode (opt-in; defaults to polling) ──
    # When True, bot receives updates via Telegram webhook instead of long polling.
    use_webhook: bool = False
    # Publicly reachable HTTPS base URL, e.g. "https://stwdz-second-brain.fly.dev"
    webhook_public_url: str = ""
    # Path where Telegram will POST updates. Must be random/secret-like.
    webhook_path: str = "/tg/webhook"
    # Optional secret token header Telegram attaches to every request
    webhook_secret: str = ""
    # Port the internal HTTP server listens on (Fly proxies :443 -> this port)
    web_port: int = 8080

    # ── Redis cache (opt-in) ──
    # If set (e.g. redis://default:pass@host:6379), embeddings and LLM responses
    # are cached there. When empty, falls back to in-process LRU.
    redis_url: str = ""
    # TTL for cached embeddings (seconds). 30 days is safe — model is stable.
    embedding_cache_ttl: int = 60 * 60 * 24 * 30

    # ── Notion integration (per-user tokens are stored in DB) ──
    # If set, enables the /notion_* commands. Used only as a feature flag.
    notion_enabled: bool = True

    @property
    def allowed_user_ids(self) -> set[int]:
        if not self.allowed_users.strip():
            return set()
        return {int(x.strip()) for x in self.allowed_users.split(",") if x.strip()}

    @property
    def admin_user_ids(self) -> set[int]:
        if not self.admin_ids.strip():
            return set()
        return {int(x.strip()) for x in self.admin_ids.split(",") if x.strip()}

    # --- LLM Provider: "groq" (бесплатно) или "openai" ---
    llm_provider: str = "groq"

    # Groq (безкоштовний — https://console.groq.com)
    # Можна вказати кілька ключів через кому: GROQ_API_KEY=key1,key2,key3
    groq_api_key: str = ""

    @property
    def groq_api_keys(self) -> list[str]:
        """Parse comma-separated Groq API keys for rotation."""
        return [k.strip() for k in self.groq_api_key.split(",") if k.strip()]
    groq_chat_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # OpenAI (платный, опционально)
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    # --- Embeddings Provider: "huggingface" (бесплатно) или "openai" ---
    embedding_provider: str = "huggingface"

    # HuggingFace (бесплатный Inference API — https://huggingface.co/settings/tokens)
    hf_api_key: str = ""
    hf_embedding_model: str = "BAAI/bge-small-en-v1.5"
    hf_embedding_dim: int = 384

    # OpenAI embeddings (если embedding_provider="openai")
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dim: int = 1536

    # Database
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/second_brain"
    )

    # Scheduler
    daily_digest_hour: int = 9

    # RAG
    chunk_size: int = 800
    chunk_overlap: int = 100
    top_k: int = 5

    @property
    def embedding_dim(self) -> int:
        if self.embedding_provider == "openai":
            return self.openai_embedding_dim
        return self.hf_embedding_dim

    @property
    def chat_model(self) -> str:
        if self.llm_provider == "openai":
            return self.openai_chat_model
        return self.groq_chat_model


settings = Settings()  # type: ignore[call-arg]
