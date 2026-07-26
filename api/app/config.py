from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables.

    All values have local-development defaults so the API starts with no
    environment at all; docker-compose overrides them for the full stack.
    """

    database_url: str = ""
    redis_url: str = ""
    # CHIRPS cache location — configurable so it can live on an external drive.
    weather_cache_dir: str = "./weather-cache"

    # Outbound email (policy documents). Unset = sending is a logged no-op, so
    # the app runs the same locally and in tests. Point these at any SMTP service.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    # Web origins allowed to call the API with credentials (comma-separated).
    # Cross-site cookies (frontend on another domain) need SameSite=None+Secure;
    # both default to the local-dev values so the app still runs bare.
    allowed_origins: str = "http://localhost:5173"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"

    model_config = {"env_prefix": "AEZ_"}

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


settings = Settings()
