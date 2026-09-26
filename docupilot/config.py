from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Local LLM (Ollama; no API key required)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_strong_model: str | None = None  # unset = reuse ollama_model
    ollama_vision_model: str = "qwen2.5vl:3b"
    ollama_num_ctx: int = Field(default=16384, gt=0)
    ollama_timeout_s: float = Field(default=300, gt=0)

    # Storage
    data_dir: Path = Path("data")

    # Local models (fastembed / ONNX, run fully offline after first download)
    dense_model: str = "BAAI/bge-small-en-v1.5"
    sparse_model: str = "Qdrant/bm25"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-12-v2"

    # Chunking (tokens are approximated as chars / 4)
    chunk_tokens: int = 450
    chunk_overlap_tokens: int = 60
    max_table_chunk_tokens: int = 900

    # Retrieval
    prefetch_k: int = 40
    top_k: int = 8
    # Rerank scores are sigmoid-normalised to [0, 1]; below this nothing is considered relevant.
    relevance_threshold: float = 0.08

    # Verification
    confidence_threshold: float = 0.5

    # Tables
    sql_max_rows: int = 200
    sql_timeout_s: float = 10.0

    # Limits
    max_files: int = 25
    max_file_mb: int = 200
    use_ocr: bool = False
    image_max_side: int = Field(default=2048, ge=256)
    image_max_frames: int = Field(default=50, gt=0)

    @property
    def qdrant_path(self) -> Path:
        return self.data_dir / "qdrant"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


@lru_cache
def get_settings() -> Settings:
    return Settings()
