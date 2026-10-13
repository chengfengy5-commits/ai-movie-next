from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import event, insert
from sqlalchemy.orm import Session

from haoai_backend.admin_credit_queries.persistence import (
    SqlAlchemyAdminCreditQueriesUnitOfWork,
)
from haoai_backend.admin_credit_queries.tables import (
    Base,
    CreditLogRow,
    UserCreditRow,
    UserRow,
)

NOW = datetime(2026, 10, 13, 10, 0)


def _user(user_id: str) -> dict[str, object]:
    return {
        "id": user_id,
        "username": user_id,
        "email": f"{user_id}@example.test",
        "hashed_password": "unused",
        "is_superuser": False,
        "membership_type": "free",
        "membership_expires_at": None,
        "avatar_url": None,
        "bio": None,
        "created_at": NOW,
        "password_updated_at": None,
    }


def test_credit_queries_keep_legacy_count_filter_sort_and_update_semantics() -> None:
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    owned_tables = [
        UserRow.__table__,
        UserCreditRow.__table__,
        CreditLogRow.__table__,
    ]
    Base.metadata.create_all(engine, tables=owned_tables)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    try:
        with engine.begin() as connection:
            connection.execute(insert(UserRow.__table__), [_user("u-1"), _user("u-2")])
            connection.execute(
                insert(UserCreditRow.__table__),
                [
                    {
                        "id": "credit-1",
                        "user_id": "u-1",
                        "credits": 5,
                        "created_at": NOW,
                        "updated_at": NOW,
                    },
                    {
                        "id": "credit-2",
                        "user_id": "u-2",
                        "credits": 12,
                        "created_at": NOW,
                        "updated_at": NOW + timedelta(minutes=1),
                    },
                ],
            )
            connection.execute(
                insert(CreditLogRow.__table__),
                [
                    {
                        "id": "log-1",
                        "user_id": "u-1",
                        "amount": -2,
                        "balance_after": 3,
                        "type": "usage",
                        "description": "first",
                        "task_id": None,
                        "business_key": "business-1",
                        "billing_unit_id": None,
                        "related_debit_id": None,
                        "created_at": NOW,
                    },
                    {
                        "id": "log-2",
                        "user_id": "u-2",
                        "amount": 4,
                        "balance_after": 16,
                        "type": "charge",
                        "description": None,
                        "task_id": None,
                        "business_key": "business-2",
                        "billing_unit_id": None,
                        "related_debit_id": None,
                        "created_at": NOW + timedelta(minutes=1),
                    },
                ],
            )

        with Session(engine) as session:
            unit = SqlAlchemyAdminCreditQueriesUnitOfWork(session)
            records, total = unit.list_user_credits(
                skip=0,
                limit=10,
                sort_field="credits",
                sort_order="desc",
            )
            assert total == 2
            assert [record.id for record in records] == ["credit-2", "credit-1"]
            assert "count" in statements[0].lower()
            assert "order by user_credits.credits desc" in statements[1].lower()

            records, total = unit.list_user_credits(
                skip=0,
                limit=10,
                sort_field="unknown_column",
                sort_order="DESC",
            )
            assert total == 2
            assert [record.id for record in records] == ["credit-1", "credit-2"]
            assert "order by user_credits.id asc" in statements[-1].lower()

            logs, total = unit.list_credit_logs(
                skip=0,
                limit=50,
                sort_field="created_at",
                sort_order="desc",
                user_id="u-2",
            )
            assert total == 1
            assert [record.id for record in logs] == ["log-2"]
            assert set(logs[0].__dataclass_fields__) == {
                "id",
                "user_id",
                "amount",
                "balance_after",
                "type",
                "description",
                "task_id",
                "created_at",
            }

            logs, total = unit.list_credit_logs(
                skip=0,
                limit=50,
                sort_field="created_at",
                sort_order="desc",
                user_id="",
            )
            assert total == 2
            assert [record.id for record in logs] == ["log-2", "log-1"]

            row = unit.load_user_credit_for_update("credit-1")
            assert row is not None
            unit.set_user_credit_amount(row, -3)
            unit.commit()
            refreshed = unit.refresh_user_credit(row)
            assert refreshed.id == row.id
            assert refreshed.credits == -3

            assert unit.get_user_credit("missing") is None
            unit.close()
    finally:
        engine.dispose()
