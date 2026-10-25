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
    enabled_operations: frozenset[str] = _SUPPORTED_OPERATIONS
    image_references_enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.registry_capabilities, RegistryCapabilities):
            raise TypeError("registry_capabilities must be RegistryCapabilities")
        if not callable(self.server_id_factory):
            raise TypeError("server_id_factory must be callable")
        if not isinstance(self.versions, SnapshotVersions):
            raise TypeError("versions must be SnapshotVersions")
        if not isinstance(self.enabled_operations, (set, frozenset)):
            raise TypeError("enabled_operations must be an explicit operation set")
        enabled_operations = frozenset(self.enabled_operations)
        if not enabled_operations.issubset(_SUPPORTED_OPERATIONS):
            raise ValueError("enabled_operations contains an unsupported operation")
        if type(self.image_references_enabled) is not bool:
            raise TypeError("image_references_enabled must be an explicit boolean")
        policy_map = dict(self.policies)
        if set(policy_map) != _SUPPORTED_OPERATIONS:
            raise ValueError("policies must configure all chat media operations")
        if any(not isinstance(value, AdmissionPolicy) for value in policy_map.values()):
            raise TypeError("all operation policies must be AdmissionPolicy values")
        object.__setattr__(self, "policies", MappingProxyType(policy_map))
        object.__setattr__(self, "enabled_operations", enabled_operations)
