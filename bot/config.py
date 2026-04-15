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

    # Groq (бесплатный — https://console.groq.com)
    groq_api_key: str = ""
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
