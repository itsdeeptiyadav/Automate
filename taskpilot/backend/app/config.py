from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # storage / workspace
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'taskpilot.db').as_posix()}"
    workspace_root: Path = REPO_DIR / "sample_data"
    invoices_subdir: str = "invoices"
    text_workspace_subdir: str = "workspace"
    max_file_bytes: int = 2_000_000
    # general document library (chat + document Q&A)
    documents_dir: Path = BACKEND_DIR / "data" / "documents"
    max_document_bytes: int = 5_000_000
    max_document_pages: int = 200
    chunk_chars: int = 900
    chunk_overlap: int = 120
    # LangGraph checkpoint store (pause/resume for approvals and questions). "" = derive from DATABASE_URL.
    checkpoint_path: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # LLM
    llm_provider: Literal["ollama", "openai", "demo"] = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "mistral"
    ollama_api_key: str = ""  # only needed for Ollama Cloud (OLLAMA_BASE_URL=https://ollama.com)
    ollama_num_ctx: int = 8192  # LOCAL Ollama only: it truncates prompts beyond this, silently dropping the instructions
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 120.0
    llm_max_repairs: int = 2

    # chat agent
    tool_calling: Literal["native", "json"] = "native"  # native = model-side tool calling; json = prompted JSON actions
    chat_max_tool_rounds: int = 6
    chat_history_messages: int = 20

    # agent limits / safety
    max_steps: int = 12
    max_tool_retries: int = 2
    retry_backoff_seconds: float = 0.5
    task_timeout_seconds: int = 300
    require_write_approval: bool = True

    # demo-only fault injection: "", "write_timeout_once", "read_flaky_once"
    fault_injection: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def checkpoint_file(self) -> str:
        """SQLite file for LangGraph checkpoints, or ':memory:' when the main database is in memory."""
        if self.checkpoint_path:
            return self.checkpoint_path
        from sqlalchemy.engine import make_url

        url = make_url(self.database_url)
        if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
            return str(Path(url.database).with_suffix(".checkpoints.db"))
        return ":memory:"

    @property
    def invoices_dir(self) -> Path:
        return self.workspace_root / self.invoices_subdir

    @property
    def text_workspace_dir(self) -> Path:
        return self.workspace_root / self.text_workspace_subdir


@lru_cache
def get_settings() -> Settings:
    return Settings()
