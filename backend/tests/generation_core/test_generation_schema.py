from dataclasses import replace

from sqlalchemy import Index, create_engine

from haoai_backend.generation_core import ExternalSubmissionRow as PackageExternalSubmissionRow
from haoai_backend.generation_core.base_schema import FOUNDATION_TABLES
from haoai_backend.generation_core.base_schema import ExternalSubmissionRow, TaskQuoteRow
from haoai_backend.generation_core.schema import (
    CatalogTable,
    GENERATION_RECORD_TABLE_NAMES,
    FOUNDATION_REQUIREMENTS,
    GENERATION_RECORD_REQUIREMENTS,
    FoundationBase,
    check_generation_record_schema,
    check_foundation_schema,
    sqlalchemy_catalog,
)
from haoai_backend.generation_core.check_expressions import canonical_check_expression


class EmptyCatalog:
    def table_names(self) -> set[str]:
        return set()

    def table(self, name: str):
        raise AssertionError(f"missing table should not be inspected: {name}")


def test_foundation_preflight_checks_real_sqlite_constraints() -> None:
    engine = create_engine("sqlite://")
    FoundationBase.metadata.create_all(engine, tables=list(FOUNDATION_TABLES))

    with engine.connect() as connection:
        result = check_foundation_schema(sqlalchemy_catalog(connection))

    assert result.ready, result.issues
    assert len(GENERATION_RECORD_TABLE_NAMES) == 8

    with engine.connect() as connection:
        catalog = sqlalchemy_catalog(connection)

        class MissingBillingUnitLink:
            def table_names(self) -> set[str]:
                return catalog.table_names()

            def table(self, name: str):
                table = catalog.table(name)
                if name != "credit_logs":
                    return table
                return replace(
                    table,
                    foreign_keys=tuple(
                        foreign_key
                        for foreign_key in table.foreign_keys
                        if foreign_key.columns != ("billing_unit_id",)
                    ),
                )

        incompatible = check_foundation_schema(MissingBillingUnitLink())

    assert not incompatible.ready
    assert any(
        issue.table == "credit_logs"
        and issue.code == "missing_or_incompatible_foreign_key"
        for issue in incompatible.issues
    )
    engine.dispose()


def test_foundation_preflight_reports_all_missing_tables() -> None:
    result = check_foundation_schema(EmptyCatalog())

    assert not result.ready
    assert {issue.table for issue in result.issues} == {
        "users",
        "ai_tasks",
        "task_submissions",
        "task_quotes",
        "billing_units",
        "execution_steps",
        "external_submissions",
        "result_evidence",
        "user_credits",
        "credit_logs",
    }


def test_generation_core_exports_external_submission_row() -> None:
    assert PackageExternalSubmissionRow is ExternalSubmissionRow


def test_generation_preflight_checks_record_columns_foreign_keys_and_unique_constraints() -> None:
    engine = create_engine("sqlite://")
    FoundationBase.metadata.create_all(engine)

    with engine.connect() as connection:
        catalog = sqlalchemy_catalog(connection)
        assert check_generation_record_schema(catalog).ready

        class MissingApplicationBindings:
            def table_names(self) -> set[str]:
                return catalog.table_names()

            def table(self, name: str) -> CatalogTable:
                table = catalog.table(name)
                if name != "application_records":
                    return table
                return replace(
                    table,
                    columns={
                        column_name: column
                        for column_name, column in table.columns.items()
                        if column_name != "committed_at"
                    },
                    foreign_keys=(),
                    unique_constraints=(),
                )

        incompatible = check_generation_record_schema(MissingApplicationBindings())

    assert not incompatible.ready
    assert any(issue.code == "missing_column" and issue.detail == "committed_at" for issue in incompatible.issues)
    assert any(issue.code == "missing_or_incompatible_foreign_key" for issue in incompatible.issues)
    assert any(issue.code == "missing_or_incompatible_unique" for issue in incompatible.issues)
    engine.dispose()


def test_foundation_catalog_rejects_partial_unvalidated_and_literal_changed_constraints() -> None:
    engine = create_engine("sqlite://")
    FoundationBase.metadata.create_all(engine, tables=list(FOUNDATION_TABLES))
    Index(
        "ux_test_partial_quote_user",
        TaskQuoteRow.user_id,
        unique=True,
        sqlite_where=TaskQuoteRow.status == "confirmed",
    ).create(engine)

    with engine.connect() as connection:
        catalog = sqlalchemy_catalog(connection)
        partial_index = next(
            item
            for item in catalog.table("task_quotes").unique_constraints
            if item.name == "ux_test_partial_quote_user"
        )
        assert partial_index.predicate == "status = 'confirmed'"

        class PartialQuoteUnique:
            def table_names(self) -> set[str]:
                return catalog.table_names()

            def table(self, name: str) -> CatalogTable:
                table = catalog.table(name)
                if name != "task_quotes":
                    return table
                return replace(
                    table,
                    unique_constraints=tuple(
                        replace(item, predicate="status = 'pending'")
                        if item.name == "uq_task_quotes_user_operation_key"
                        else item
                        for item in table.unique_constraints
                    ),
                )

        partial = check_foundation_schema(PartialQuoteUnique())

        class UnvalidatedBillingLink:
            def table_names(self) -> set[str]:
                return catalog.table_names()

            def table(self, name: str) -> CatalogTable:
                table = catalog.table(name)
                if name != "credit_logs":
                    return table
                return replace(
                    table,
                    foreign_keys=tuple(
                        replace(item, validated=False)
                        if item.columns == ("billing_unit_id",)
                        else item
                        for item in table.foreign_keys
                    ),
                )

        unvalidated = check_foundation_schema(UnvalidatedBillingLink())

        class ChangedBillingLiteral:
            def table_names(self) -> set[str]:
                return catalog.table_names()

            def table(self, name: str) -> CatalogTable:
                table = catalog.table(name)
                if name != "ai_tasks":
                    return table
                return replace(
                    table,
                    checks=tuple(
                        replace(
                            item,
                            expression=item.expression.replace(
                                "'legacy_unreconciled'", "'LEGACY_UNRECONCILED'"
                            ),
                        )
                        if item.name == "ck_ai_tasks_billing_status"
                        else item
                        for item in table.checks
                    ),
                )

        changed_literal = check_foundation_schema(ChangedBillingLiteral())

        class EquivalentCheckFormatting:
            def table_names(self) -> set[str]:
                return catalog.table_names()

            def table(self, name: str) -> CatalogTable:
                table = catalog.table(name)
                if name != "ai_tasks":
                    return table
                return replace(
                    table,
                    checks=tuple(
                        replace(
                            item,
                            expression=" ( ( "
                            + item.expression.replace(" IN ", "   IN   ")
                            + " ) ) ",
                        )
                        if item.name == "ck_ai_tasks_billing_status"
                        else item
                        for item in table.checks
                    ),
                )

        equivalent_formatting = check_foundation_schema(EquivalentCheckFormatting())

    assert not partial.ready
    assert any(issue.code == "unique_not_effective_validated_and_global" for issue in partial.issues)
    assert not unvalidated.ready
    assert any(issue.code == "foreign_key_not_effective_and_validated" for issue in unvalidated.issues)
    assert not changed_literal.ready
    assert any(issue.code == "missing_or_incompatible_check" for issue in changed_literal.issues)
    assert equivalent_formatting.ready, equivalent_formatting.issues
    engine.dispose()


_ROOT_POSTGRES_CHECKS = {
    "ai_tasks": {
        "ck_ai_tasks_billing_status": (
            "billing_status::text = ANY (ARRAY["
            "'legacy_unreconciled'::character varying, 'unbilled'::character varying, "
            "'charged'::character varying, 'partially_refunded'::character varying, "
            "'refunded'::character varying, 'no_charge'::character varying]::text[])"
        ),
    },
    "billing_units": {
        "ck_billing_units_billing_status": (
            "billing_status::text = ANY (ARRAY["
            "'unbilled'::character varying, 'charged'::character varying, "
            "'refunded'::character varying, 'no_charge'::character varying]::text[])"
        ),
        "ck_billing_units_result_status": (
            "result_status::text = ANY (ARRAY["
            "'pending'::character varying, 'partial'::character varying, "
            "'succeeded'::character varying, 'failed'::character varying, "
            "'unknown'::character varying]::text[])"
        ),
    },
}


def test_postgres_reflected_checks_match_only_safe_semantic_equivalents() -> None:
    engine = create_engine("sqlite://")
    FoundationBase.metadata.create_all(engine, tables=list(FOUNDATION_TABLES))
    with engine.connect() as connection:
        catalog = sqlalchemy_catalog(connection)

        def reflected(overrides: dict[str, dict[str, str]]):
            class ReflectedChecks:
                def table_names(self) -> set[str]:
                    return catalog.table_names()

                def table(self, name: str) -> CatalogTable:
                    table = catalog.table(name)
                    table_overrides = overrides.get(name, {})
                    if not table_overrides:
                        return table
                    return replace(
                        table,
                        checks=tuple(
                            replace(item, expression=table_overrides[item.name])
                            if item.name in table_overrides
                            else item
                            for item in table.checks
                        ),
                    )

            return ReflectedChecks()

        compatible = check_foundation_schema(reflected(_ROOT_POSTGRES_CHECKS))
        operation_cast = check_foundation_schema(
            reflected(
                {
                    **_ROOT_POSTGRES_CHECKS,
                    "task_submissions": {
                        "ck_task_submissions_operation_nonempty": "length(operation::text) > 0"
                    },
                }
            )
        )
        operation_varchar_to_text = check_foundation_schema(
            reflected(
                {
                    **_ROOT_POSTGRES_CHECKS,
                    "task_submissions": {
                        "ck_task_submissions_operation_nonempty": (
                            "length(operation::character varying::text) > 0"
                        )
                    },
                }
            )
        )
        changed_literal = check_foundation_schema(
            reflected(
                {
                    "ai_tasks": {
                        "ck_ai_tasks_billing_status": _ROOT_POSTGRES_CHECKS["ai_tasks"][
                            "ck_ai_tasks_billing_status"
                        ].replace("'charged'::character varying", "'CHARGED'::character varying")
                    }
                }
            )
        )
        changed_precedence = check_foundation_schema(
            reflected(
                {
                    "ai_tasks": {
                        "ck_ai_tasks_billing_status": _ROOT_POSTGRES_CHECKS["ai_tasks"][
                            "ck_ai_tasks_billing_status"
                        ]
                        + " OR billing_status = 'retired'"
                    }
                }
            )
        )
        billing_status_dimension_change = check_foundation_schema(
            reflected(
                {
                    "ai_tasks": {
                        "ck_ai_tasks_billing_status": _ROOT_POSTGRES_CHECKS["ai_tasks"][
                            "ck_ai_tasks_billing_status"
                        ].replace(
                            "billing_status::text = ANY (",
                            "billing_status::text[]::text = ANY (",
                        )
                    }
                }
            )
        )
        operation_dimension_change = check_foundation_schema(
            reflected(
                {
                    "task_submissions": {
                        "ck_task_submissions_operation_nonempty": (
                            "length(operation::text[]::text) > 0"
                        )
                    }
                }
            )
        )
        meaningful_cast = check_foundation_schema(
            reflected(
                {
                    "ai_tasks": {
                        "ck_ai_tasks_billing_status": _ROOT_POSTGRES_CHECKS["ai_tasks"][
                            "ck_ai_tasks_billing_status"
                        ].replace("billing_status::text", "billing_status::varchar(8)")
                    }
                }
            )
        )
        unsupported_cast = check_foundation_schema(
            reflected(
                {
                    "ai_tasks": {
                        "ck_ai_tasks_billing_status": _ROOT_POSTGRES_CHECKS["ai_tasks"][
                            "ck_ai_tasks_billing_status"
                        ].replace("billing_status::text", "billing_status::citext")
                    }
                }
            )
        )
        malformed = check_foundation_schema(
            reflected({"ai_tasks": {"ck_ai_tasks_billing_status": "billing_status ="}})
        )

    assert compatible.ready, compatible.issues
    assert operation_cast.ready, operation_cast.issues
    assert operation_varchar_to_text.ready, operation_varchar_to_text.issues
    for result in (changed_literal, changed_precedence, meaningful_cast, unsupported_cast, malformed):
        assert not result.ready
        assert any(
            issue.table == "ai_tasks" and issue.code == "missing_or_incompatible_check"
            for issue in result.issues
        )
    assert not billing_status_dimension_change.ready
    assert any(
        issue.table == "ai_tasks" and issue.code == "missing_or_incompatible_check"
        for issue in billing_status_dimension_change.issues
    )
    assert not operation_dimension_change.ready
    assert any(
        issue.table == "task_submissions" and issue.code == "missing_or_incompatible_check"
        for issue in operation_dimension_change.issues
    )

    scalar_operation = canonical_check_expression(
        "operation = 'text.stream'",
        table_name="generation_work_items",
        string_columns=frozenset({"operation"}),
    )
    operation_array_round_trip = canonical_check_expression(
        "operation::text[]::text = 'text.stream'",
        table_name="generation_work_items",
        string_columns=frozenset({"operation"}),
    )
    assert scalar_operation is not None
    assert operation_array_round_trip is not None
    assert scalar_operation != operation_array_round_trip
    engine.dispose()


def test_check_ast_preserves_arithmetic_and_boolean_precedence() -> None:
    string_columns = frozenset({"status"})
    boolean_flat = canonical_check_expression(
        "status = 'active' OR status = 'paused' AND status = 'done'",
        table_name="records",
        string_columns=string_columns,
    )
    boolean_grouped = canonical_check_expression(
        "(status = 'active' OR status = 'paused') AND status = 'done'",
        table_name="records",
        string_columns=string_columns,
    )
    arithmetic_flat = canonical_check_expression(
        "total_amount = unit_price + unit_count * 2",
        table_name="task_quotes",
        string_columns=frozenset(),
    )
    arithmetic_grouped = canonical_check_expression(
        "total_amount = (unit_price + unit_count) * 2",
        table_name="task_quotes",
        string_columns=frozenset(),
    )

    assert boolean_flat is not None and boolean_grouped is not None
    assert arithmetic_flat is not None and arithmetic_grouped is not None
    assert boolean_flat != boolean_grouped
    assert arithmetic_flat != arithmetic_grouped


def test_all_source_checks_are_inside_the_supported_comparison_grammar() -> None:
    for requirements in (FOUNDATION_REQUIREMENTS, GENERATION_RECORD_REQUIREMENTS):
        for table_name, requirement in requirements.items():
            string_columns = frozenset(
                name
                for name, column in requirement.columns.items()
                if column.kind in {"string", "text"}
            )
            for check in requirement.checks:
                assert canonical_check_expression(
                    check.expression,
                    table_name=table_name,
                    string_columns=string_columns,
                ) is not None, (table_name, check.name, check.expression)
