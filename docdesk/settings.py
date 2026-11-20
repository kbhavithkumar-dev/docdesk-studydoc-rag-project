"""Read server settings from environment variables or a local .env file."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re

from dotenv import load_dotenv

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_CHUNKS = 400
MAX_INDEX_CHUNKS = 5000
MAX_DOCUMENTS = 250
DIMENSIONS = 768


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    api_key: str = field(default="", repr=False)
    embedding_model: str = "gemini-embedding-001"
    chat_model: str = "gemini-2.5-flash-lite"
    min_similarity: float = 0.35
    username: str = "docdesk"
    password: str = field(default="", repr=False)
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]")

    @classmethod
    def from_env(cls):
        # Exported variables take precedence over .env, which is useful in Docker.
        load_dotenv(Path.cwd() / ".env")
        hosts = tuple(
            host.strip()
            for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
            if host.strip()
        )
        password = os.getenv("APP_PASSWORD", "")
        username = os.getenv("APP_USERNAME", "docdesk")
        threshold = float(os.getenv("MIN_SIMILARITY", "0.35"))
        if not 0 <= threshold <= 1:
            raise ValueError("MIN_SIMILARITY must be between 0 and 1.")
        local_hosts = {"localhost", "127.0.0.1", "[::1]"}
        if not hosts or any("*" in host for host in hosts):
            raise ValueError("ALLOWED_HOSTS must contain explicit host names.")
        if (password or set(hosts) - local_hosts) and (
            len(password) < 16 or not password.isascii()
            or not username or not username.isascii() or ":" in username
        ):
            raise ValueError("Shared deployments need an ASCII password of at least 16 characters and a valid username.")
        models = [
            os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"),
            os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash-lite"),
        ]
        if any(not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", model) for model in models):
            raise ValueError("Use plain Gemini model names, without the models/ prefix.")
        return cls(
            data_dir=Path(os.getenv("DATA_DIR", "data")),
            api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            embedding_model=models[0],
            chat_model=models[1],
            min_similarity=threshold,
            username=username,
            password=password,
            allowed_hosts=hosts,
        )
