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

    model_config = {"env_prefix": "AEZ_"}


settings = Settings()
