import os
import sys

from app.core.config import get_settings
from app.core.database import get_engine
from app.models.domain import Base


def reset_database() -> None:
    app_env = os.getenv("APP_ENV")
    if app_env and app_env.lower() in {"production", "prod"}:
        raise RuntimeError("Refusing to run reset_db.py in production environment.")

    settings = get_settings()
    if settings.APP_ENV == "production":
        raise RuntimeError("Refusing to run reset_db.py when APP_ENV=production.")

    engine = get_engine()
    print("Dropping all existing Version 2 domain tables in the database...")
    Base.metadata.drop_all(bind=engine)
    print("All Version 2 tables dropped successfully!")


if __name__ == "__main__":
    try:
        reset_database()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
