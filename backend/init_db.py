from app.core.database import UnitOfWork, get_session_factory
from app.domain.rbac_catalog import seed_rbac_catalog


def seed_foundation() -> None:
    """
    Seeds the canonical RBAC roles, atomic permissions, and role-permission mappings.
    Schema creation is managed exclusively via Alembic (`alembic upgrade head`), never create_all().
    """
    factory = get_session_factory()
    with UnitOfWork(factory) as uow:
        assert uow.session is not None
        summary = seed_rbac_catalog(uow.session)
        print(f"RBAC catalog seeded cleanly: {summary}")


if __name__ == "__main__":
    seed_foundation()
