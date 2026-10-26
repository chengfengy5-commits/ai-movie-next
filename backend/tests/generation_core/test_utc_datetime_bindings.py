from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, DateTime, MetaData, String, Table, create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from haoai_backend.generation_core.base_schema import AITaskRow, Base, BillingUnitRow


def _bound_value(column_type, value, dialect):
    processor = column_type.bind_processor(dialect)
    return value if processor is None else processor(value)


def _timezone_columns():
    return [
        column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if getattr(column.type, "timezone", False)
    ]


def test_timezone_aware_generation_columns_bind_naive_values_as_utc_for_postgres():
    columns = _timezone_columns()
    assert columns

    value = datetime(2026, 10, 12, 12, 0)
    expected = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    dialect = postgresql.dialect()

    for column in columns:
        bound = _bound_value(column.type, value, dialect)
        assert bound.tzinfo is timezone.utc, column
        assert bound == expected, column
        assert column.type.compile(dialect=dialect) == "TIMESTAMP WITH TIME ZONE"


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (
            datetime(2026, 10, 12, 12, 0),
            datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc),
        ),
        (
            datetime(2026, 10, 12, 20, 0, tzinfo=timezone(timedelta(hours=8))),
            datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc),
        ),
    ),
    ids=("naive-is-utc", "offset-aware-converts-to-utc"),
)
def test_billing_unit_datetime_bind_processor_normalizes_to_utc(value, expected):
    column_type = BillingUnitRow.__table__.c.first_intent_at.type
    bound = _bound_value(column_type, value, postgresql.dialect())

    assert bound.tzinfo is timezone.utc
    assert bound == expected


def test_sqlite_session_round_trip_and_comparison_use_the_same_utc_instant():
    column_type = BillingUnitRow.__table__.c.first_intent_at.type
    metadata = MetaData()
    table = Table(
        "utc_datetime_round_trip",
        metadata,
        Column("id", String(8), primary_key=True),
        Column("occurred_at", column_type, nullable=False),
    )
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)

    values = (
        datetime(2026, 10, 12, 12, 0),
        datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 12, 20, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    expected = datetime(2026, 10, 12, 12, 0)

    try:
        with Session(engine) as session:
            session.execute(
                table.insert(),
                [
                    {"id": str(index), "occurred_at": value}
                    for index, value in enumerate(values)
                ],
            )
            session.commit()

        with Session(engine) as session:
            stored = session.execute(
                select(table.c.occurred_at).order_by(table.c.id)
            ).scalars().all()
            assert stored == [expected, expected, expected]

            for value in values:
                matching = session.execute(
                    select(table.c.id).where(table.c.occurred_at == value)
                ).scalars().all()
                assert matching == ["0", "1", "2"]
    finally:
        engine.dispose()


def test_legacy_ai_task_lease_and_timestamps_keep_naive_datetime_binding():
    dialect = postgresql.dialect()
    value = datetime(2026, 10, 12, 12, 0)

    for name in ("lease_until", "created_at", "updated_at"):
        column_type = AITaskRow.__table__.c[name].type
        assert type(column_type) is DateTime
        assert column_type.timezone is False
        bound = _bound_value(column_type, value, dialect)
        assert bound == value
        assert bound.tzinfo is None
