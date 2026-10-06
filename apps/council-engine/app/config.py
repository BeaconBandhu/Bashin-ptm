from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings' env_file loading below populates OUR Settings object
# only — it does not inject values into the process's real os.environ.
# Third-party libraries that read os.environ directly (LangSmith's tracing
# client, in particular: LANGCHAIN_TRACING_V2/LANGCHAIN_API_KEY/
# LANGCHAIN_PROJECT) would silently never see .env values without this.
load_dotenv()


class Settings(BaseSettings):
    """Runtime configuration. All values overridable via environment variables
    (see .env.example) so nothing is ever hardcoded across dev/preview/prod."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"

    # Persistence. When database_url is unset the engine falls back to the
    # in-memory store (app/db/memory_store.py) so unit tests and local demos
    # run with zero infra. Production must set both.
    database_url: str | None = None
    redis_url: str | None = None

    # Ticket store (tickets/queries/decisions/governance-verdict history —
    # what the human console and the /v1/tickets endpoints read from). When
    # mongodb_uri is unset this falls back to an in-process in-memory store
    # (app/tickets/memory_store.py) — same pattern as database_url/redis_url
    # above, so local dev needs zero infra and a real deployment is a config
    # change, not a rewrite (see app/tickets/mongo_store.py).
    mongodb_uri: str | None = None
    mongodb_db_name: str = "ai_teammate"

    # Local-dev CORS: apps/web (`next dev`) and apps/council-engine
    # (`uvicorn`) run as two separate localhost ports until this runs behind
    # `vercel dev`'s Services binding — see vercel.json.
    cors_allow_origins: list[str] = ["http://localhost:3000"]

    # Service-to-service auth: Next.js signs a short-lived JWT on every
    # request over the private Vercel Services binding.
    internal_jwt_secret: str = "dev-only-insecure-secret-change-me"
    internal_jwt_audience: str = "council-engine"

    # AI Gateway (Vercel) — unset in Phase 0, wired in Phase 1+.
    ai_gateway_api_key: str | None = None

    # Customer-support domain's Council tier — the only place a paid LLM
    # call happens (RAG retrieval is TF-IDF, $0). Unset either key and that
    # provider is simply unavailable to the council's client selection.
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    # gpt-5-nano: $0.05/$0.40 per 1M input/output tokens — cheapest current
    # OpenAI model, confirmed via web search (Sept 2026 pricing) rather than
    # assumed from training data, given real money is on the line.
    openai_model: str = "gpt-5-nano"
    # Haiku 4.5: $1/$5 per 1M input/output tokens (confirmed Sept 2026).
    anthropic_model: str = "claude-haiku-4-5-20251001"

    # Groq (GroqCloud) — Council Triage role. OpenAI-compatible API, so
    # GroqClient reuses the openai SDK pointed at a different base_url.
    # Self-serve catalog is GPT-OSS models (Llama now needs an enterprise
    # conversation); free tier (~500K tokens/day) should cover all Triage
    # traffic at our volume. Confirmed via web search, Sept 2026.
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-20b"

    # Hard spend caps in USD (real, computed from token usage — see
    # app/spend/tracker.py). The ai_spend_limit guardrail blocks further
    # paid calls to a provider once its cap is reached and routes straight
    # to human escalation instead of erroring out.
    max_openai_spend_usd: float = 4.00
    max_anthropic_spend_usd: float = 6.00
    max_groq_spend_usd: float = 2.00

    # Latency/behavior tuning
    council_confidence_threshold: float = 0.72
    node_retry_max: int = 2

    # RAG tier-1 gate (customer-support domain): TF-IDF cosine score above
    # this routes straight to the matched FAQ answer, $0 cost. Calibrated
    # empirically against the corpus, not theoretically derived — TF-IDF
    # scores for short queries against a small corpus are noisy, so this is
    # set high enough to bias toward escalating-when-unsure (a wasted
    # Council call costs a fraction of a cent) rather than confidently
    # answering wrong (a real trust problem). See app/rag/retriever.py.
    rag_confidence_threshold: float = 0.55

    @property
    def has_database(self) -> bool:
        return bool(self.database_url)

    @property
    def has_mongodb(self) -> bool:
        return bool(self.mongodb_uri)

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
