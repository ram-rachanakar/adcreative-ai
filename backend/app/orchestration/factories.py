"""Late-bound provider/storage factories shared by the API and the Celery worker (the worker has no request context).
Tests replace these module attributes; production reads configuration."""
from app.ai.image import ImageGenerationProvider, get_image_provider
from app.ai.llm import LLMProvider, get_llm_provider
from app.storage.base import Storage, get_storage

image_provider = get_image_provider
llm_provider = get_llm_provider
storage = get_storage


def make_image_provider() -> ImageGenerationProvider:
    return image_provider()


def make_storage() -> Storage:
    return storage()


def make_llm_provider() -> LLMProvider:
    return llm_provider()
