import os
from pathlib import Path
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent / ".env"

load_dotenv(ENV_FILE, override=True)

class Settings:

    provider_name: str = os.getenv("PROVIDER", "meta").strip().lower()

    meta_access_token: str = os.getenv("META_ACCESS_TOKEN", "")
    meta_phone_number_id: str = os.getenv("META_PHONE_NUMBER_ID", "")
    meta_verify_token: str = os.getenv("META_VERIFY_TOKEN", "meu_token_de_verificacao")

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")

    db_url: str = os.getenv("SQLALCHEMY_DB_URL")

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

def get_decider_client():
    from .clients.decision_model import ElephantDecideClient

    return ElephantDecideClient(api_key=settings.openrouter_api_key)
