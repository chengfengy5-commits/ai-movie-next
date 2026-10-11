"""Strict autospecced test doubles for the frozen P1–P6 public ports.

Unconfigured methods are mocks, not successful domain outcomes. Tests must set
explicit return values for every interaction that represents business truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar, cast
from unittest.mock import create_autospec

from haoai_backend.generation_contracts.ports import (
    AdmissionUnitOfWork,
    GenerationAdmissionUnitOfWork,
    GenerationCatalog,
    GenerationExecutionPort,
    GenerationMediaStore,
    GenerationProviderPort,
    GenerationResultApplicationPort,
    GenerationWorkerRuntime,
    LifecycleUnitOfWork,
    SubmissionRecoveryPort,
)

Port = TypeVar("Port")


def _fake(protocol: type[Port]) -> Port:
    return cast(Port, create_autospec(protocol, instance=True, spec_set=True))


@dataclass(frozen=True, slots=True)
class FakeGenerationPorts:
    admission: GenerationCatalog
    admission_uow: GenerationAdmissionUnitOfWork
    execution: GenerationExecutionPort
    lifecycle: LifecycleUnitOfWork
    provider: GenerationProviderPort
    media: GenerationMediaStore
    application: GenerationResultApplicationPort
    runtime: GenerationWorkerRuntime
    recovery: SubmissionRecoveryPort


def fake_generation_ports() -> FakeGenerationPorts:
    """Build P1–P6 fakes whose callable surface matches the public protocols."""

    return FakeGenerationPorts(
        admission=_fake(GenerationCatalog),
        admission_uow=_fake(AdmissionUnitOfWork),
        execution=_fake(GenerationExecutionPort),
        lifecycle=_fake(LifecycleUnitOfWork),
        provider=_fake(GenerationProviderPort),
        media=_fake(GenerationMediaStore),
        application=_fake(GenerationResultApplicationPort),
        runtime=_fake(GenerationWorkerRuntime),
        recovery=_fake(SubmissionRecoveryPort),
    )


__all__ = ("FakeGenerationPorts", "fake_generation_ports")
