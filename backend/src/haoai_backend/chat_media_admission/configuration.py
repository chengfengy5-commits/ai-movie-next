"""Explicit server-owned dependencies for the shared admission producer."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from haoai_backend.generation_catalog.admission import AdmissionPolicy
from haoai_backend.generation_credentials.application import RegistryCapabilities
from haoai_backend.generation_credentials.snapshot_producer import SnapshotVersions

_SUPPORTED_OPERATIONS = frozenset(
    {"image.single", "video.single", "image.batch"}
)


@dataclass(frozen=True, slots=True)
class ChatMediaAdmissionConfiguration:
    """Server capabilities used to bind the actual generation admission producer."""

    registry_capabilities: RegistryCapabilities
    server_id_factory: Callable[[], str]
    policies: Mapping[str, AdmissionPolicy]
    versions: SnapshotVersions

    def __post_init__(self) -> None:
        if not isinstance(self.registry_capabilities, RegistryCapabilities):
            raise TypeError("registry_capabilities must be RegistryCapabilities")
        if not callable(self.server_id_factory):
            raise TypeError("server_id_factory must be callable")
        if not isinstance(self.versions, SnapshotVersions):
            raise TypeError("versions must be SnapshotVersions")
        policy_map = dict(self.policies)
        if set(policy_map) != _SUPPORTED_OPERATIONS:
            raise ValueError("policies must configure all chat media operations")
        if any(not isinstance(value, AdmissionPolicy) for value in policy_map.values()):
            raise TypeError("all operation policies must be AdmissionPolicy values")
        object.__setattr__(self, "policies", MappingProxyType(policy_map))
