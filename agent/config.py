import os
from pathlib import Path
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent / ".env"

load_dotenv(ENV_FILE, override=True)

class Settings:

    provider_name: str = os.getenv("PROVIDER", "meta").strip().lower()

    sentry_dsn: str = os.getenv("SENTRY_DSN", "").strip()
    sentry_environment: str = os.getenv("SENTRY_ENVIRONMENT", "development").strip()

    meta_access_token: str = os.getenv("META_ACCESS_TOKEN", "")
    meta_phone_number_id: str = os.getenv("META_PHONE_NUMBER_ID", "")
    meta_verify_token: str = os.getenv("META_VERIFY_TOKEN", "meu_token_de_verificacao")

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    # modelo principal e o reserva, usado quando o principal falha (ver agent/llm_call.py). Nome que
    # começa com "gpt-" (ou "openai/") vai pra OpenAI (agent/llm_openai.py); o resto, pro Gemini.
    # Ex. pra usar o GPT Luna com o Gemini 3.5 de reserva: LLM_MODEL=gpt-6-luna,
    # LLM_FALLBACK_MODEL=gemini-flash-lite-latest e OPENAI_API_KEY preenchida
    llm_model: str = os.getenv("LLM_MODEL", "gemini-flash-lite-latest")
    llm_fallback_model: str = os.getenv("LLM_FALLBACK_MODEL", "gemini-3.1-flash-lite")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    # opcional: esforço de raciocínio dos GPT (ex.: "minimal" ou "low", conforme o modelo aceitar)
    openai_reasoning_effort: str = os.getenv("OPENAI_REASONING_EFFORT", "")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")

    db_url: str = os.getenv("SQLALCHEMY_DB_URL")

    dashboard_signing_key: str = os.getenv("DASHBOARD_SIGNING_KEY", "")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8001").rstrip("/")

    mercadopago_access_token: str = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")
    mercadopago_webhook_secret: str = os.getenv("MERCADOPAGO_WEBHOOK_SECRET", "")

settings = Settings()

def get_provider():

    if settings.provider_name == "meta":

        from .providers.meta import MetaProvider

        return MetaProvider(
            access_token=settings.meta_access_token,
            phone_number_id=settings.meta_phone_number_id,
            verify_token=settings.meta_verify_token,
        )


def get_llm_client():
    from .clients.llm import GeminiClient

    return GeminiClient(api_key=settings.gemini_api_key)

def get_payment_client():
    from .clients.mercadopago import MercadoPagoClient

    return MercadoPagoClient(access_token=settings.mercadopago_access_token)

def get_decider_client():
    from .clients.decision_model import MercuryDecideClient

    return MercuryDecideClient(api_key=settings.openrouter_api_key)
