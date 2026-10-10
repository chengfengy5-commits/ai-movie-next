from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.exc import (
    ArgumentError,
    CompileError,
    OperationalError,
    SQLAlchemyError,
)

from haoai_backend.admin_tasks.domain import AdminTaskFilters
from haoai_backend.admin_tasks.persistence import (
    SqlAlchemyAdminTaskUnitOfWork,
    admin_task_unit_of_work_factory,
)
from haoai_backend.admin_tasks.tables import AdminTask, mapper_registry, metadata

from admin_tasks_support import NOW, admin_task_database, make_task_row, seed_admin_tasks


def test_sql_queries_consume_count_and_list_results_without_business_dml(
    admin_task_database,
) -> None:
    seed_admin_tasks(
        admin_task_database,
        [
            make_task_row(
                "task-a", "user-a", type="image", model_name="model-A-one",
                created_at=NOW,
            ),
            make_task_row(
                "task-b", "user-a", type="image", model_name="model_B_two",
                created_at=NOW,
            ),
            make_task_row(
                "task-c", "user-b", type="video", model_name="other",
                created_at=NOW,
            ),
        ],
    )
    before = admin_task_database.snapshot()
    statements: list[tuple[str, object]] = []

    def capture(_connection, _cursor, statement, parameters, _context, _many) -> None:
        statements.append((statement, parameters))

    event.listen(admin_task_database.engine, "before_cursor_execute", capture)
    unit = SqlAlchemyAdminTaskUnitOfWork(admin_task_database.session_factory())
    try:
        filters = AdminTaskFilters(user_id="user-a", task_type="image")
        total = unit.count_tasks(filters)
        items = unit.list_tasks(
            filters,
            sort_field="created_at",
            sort_order="desc",
            offset=0,
            limit=10,
        )
        wildcard = AdminTaskFilters(model_name="model_%")
        assert unit.count_tasks(wildcard) == 2
    finally:
        unit.rollback()
        unit.close()
        event.remove(admin_task_database.engine, "before_cursor_execute", capture)

    sql = [statement.lower() for statement, _ in statements]
    assert total == 2
    assert [item.id for item in items] == ["task-a", "task-b"]
    assert "count(" in sql[0]
    assert sql[1].startswith("select ai_tasks.")
    assert "count(" in sql[2]
    assert any("%model_%%" in repr(parameters) for _, parameters in statements)
    assert not any(
        statement.lstrip().lower().startswith(("insert ", "update ", "delete "))
        for statement, _ in statements
    )
    assert admin_task_database.snapshot() == before
    assert admin_task_database.session_factory.created[-1].close_calls == 1


def test_raw_sort_fallback_direction_and_negative_paging_reach_sql(
    admin_task_database,
) -> None:
    seed_admin_tasks(
        admin_task_database,
        [
            make_task_row("task-b", "user-a", created_at=NOW),
            make_task_row("task-a", "user-b", created_at=NOW),
        ],
    )
    statements: list[tuple[str, object]] = []

    def capture(_connection, _cursor, statement, parameters, _context, _many) -> None:
        statements.append((statement.lower(), parameters))

    event.listen(admin_task_database.engine, "before_cursor_execute", capture)
    unit = SqlAlchemyAdminTaskUnitOfWork(admin_task_database.session_factory())
    try:
        rows = unit.list_tasks(
            AdminTaskFilters(),
            sort_field="not_a_column",
            sort_order="DESC",
            offset=-4,
            limit=-1,
        )
    finally:
        unit.rollback()
        unit.close()
        event.remove(admin_task_database.engine, "before_cursor_execute", capture)

    assert [item.id for item in rows] == ["task-a", "task-b"]
    select_sql, parameters = next(
        (statement, parameters)
        for statement, parameters in statements
        if statement.startswith("select ai_tasks.")
    )
    assert "order by ai_tasks.id asc" in select_sql
    assert "order by ai_tasks.id asc," not in select_sql
    assert parameters[-2:] == (-1, -4)


def test_declarative_class_attributes_are_not_unknown_sort_fields(
    admin_task_database,
) -> None:
    seed_admin_tasks(admin_task_database, [make_task_row("task-a", "user-a")])
    assert AdminTask.metadata is metadata
    assert AdminTask.registry is mapper_registry
    assert AdminTask.__tablename__ == "ai_tasks"
    assert AdminTask.__doc__ == "AI 任务记录 - 跟踪 AI 处理状态"
    check_constraint = AdminTask.__table_args__[0]
    assert check_constraint.name == "ck_ai_tasks_billing_status"
    assert check_constraint in AdminTask.__table__.constraints

    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement.lower())

    event.listen(admin_task_database.engine, "before_cursor_execute", capture)
    unit = SqlAlchemyAdminTaskUnitOfWork(admin_task_database.session_factory())
    try:
        for sort_field in (
            "metadata",
            "registry",
            "__tablename__",
            "__doc__",
            "__table_args__",
        ):
            assert hasattr(AdminTask, sort_field)
            with pytest.raises(SQLAlchemyError):
                unit.list_tasks(
                    AdminTaskFilters(),
                    sort_field=sort_field,
                    sort_order="asc",
                    offset=0,
                    limit=10,
                )
            assert not any(
                "order by ai_tasks.id asc" in statement
                or "order by ai_tasks.id desc" in statement
                for statement in statements
            )
            unit.rollback()
    finally:
        unit.rollback()
        unit.close()
        event.remove(admin_task_database.engine, "before_cursor_execute", capture)

    assert admin_task_database.session_factory.created[-1].close_calls == 1


@pytest.mark.parametrize("sort_order", ("asc", "desc"))
def test_docstring_sort_preserves_compile_error_for_both_directions(
    admin_task_database,
    sort_order: str,
) -> None:
    seed_admin_tasks(admin_task_database, [make_task_row("task-a", "user-a")])
    unit = SqlAlchemyAdminTaskUnitOfWork(admin_task_database.session_factory())
    try:
        with pytest.raises(CompileError):
            unit.list_tasks(
                AdminTaskFilters(),
                sort_field="__doc__",
                sort_order=sort_order,
                offset=0,
                limit=10,
            )
    finally:
        unit.rollback()
        unit.close()

    assert admin_task_database.session_factory.created[-1].close_calls == 1


def test_existing_relationship_sort_error_is_not_replaced_by_id_fallback(
    admin_task_database,
) -> None:
    seed_admin_tasks(admin_task_database, [make_task_row("task-a", "user-a")])
    unit = SqlAlchemyAdminTaskUnitOfWork(admin_task_database.session_factory())
    try:
        try:
            unit.list_tasks(
                AdminTaskFilters(),
                sort_field="user",
                sort_order="asc",
                offset=0,
                limit=10,
            )
        except (ArgumentError, CompileError, OperationalError):
            pass
        else:
            raise AssertionError("sorting a mapped relationship should preserve its SQL error")
    finally:
        unit.rollback()
        unit.close()

    assert admin_task_database.session_factory.created[-1].close_calls == 1


def test_detail_is_global_and_keeps_json_columns_as_raw_strings(admin_task_database) -> None:
    raw_request = "{broken-json " + ("x" * 3000)
    raw_result = "not-json " + ("y" * 4000)
    seed_admin_tasks(
        admin_task_database,
        [
            make_task_row(
                "task-other", "user-b",
                request_data=raw_request,
                result=raw_result,
            )
        ],
    )
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement.lower())

    event.listen(admin_task_database.engine, "before_cursor_execute", capture)
    unit = SqlAlchemyAdminTaskUnitOfWork(admin_task_database.session_factory())
    try:
        detail = unit.load_task_by_id("task-other")
    finally:
        unit.rollback()
        unit.close()
        event.remove(admin_task_database.engine, "before_cursor_execute", capture)

    assert detail is not None
    assert detail.request_data == raw_request
    assert detail.result == raw_result
    assert "where ai_tasks.id =" in statements[0]
    assert "where ai_tasks.user_id" not in statements[0]
