import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/orbit.db")
    secret_key: str = os.getenv("SECRET_KEY", "")
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")
    cors_origins: str = os.getenv("CORS_ORIGINS", "")
    environment: str = os.getenv("ENVIRONMENT", "development")


settings = Settings()
