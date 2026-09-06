from abc import ABC, abstractmethod

from app.acquisition.schemas import (
    AcquiredArtifact,
    AcquisitionRequest,
    AcquisitionResult,
    ProviderIdentity,
)
from app.models.common import AssetType


class AcquisitionError(Exception):
    """Base error for the acquisition framework."""


class AcquisitionValidationError(AcquisitionError):
    """The request is not valid for this provider."""


class AcquisitionConfigurationError(AcquisitionError):
    """Required provider configuration is missing or unusable."""


class AcquisitionProvider(ABC):
    """Abstract source of investigation assets.

    Concrete providers live under app.acquisition.providers.
    """

    @property
    @abstractmethod
    def identity(self) -> ProviderIdentity:
        """Stable provider id, display name, and supported asset types."""

    def supports(self, request: AcquisitionRequest) -> bool:
        identity = self.identity
        return (
            request.provider_id == identity.id
            and request.asset_type in identity.supported_asset_types
        )

    def validate(self, request: AcquisitionRequest) -> None:
        """Reject requests this provider cannot honor. Does not perform I/O."""
        identity = self.identity
        if request.provider_id != identity.id:
            raise AcquisitionValidationError(
                f"provider '{identity.id}' cannot handle provider_id '{request.provider_id}'"
            )
        if request.asset_type not in identity.supported_asset_types:
            supported = ", ".join(sorted(item.value for item in identity.supported_asset_types)) or "none"
            raise AcquisitionValidationError(
                f"provider '{identity.id}' does not support asset type '{request.asset_type.value}' "
                f"(supported: {supported})"
            )
        self.validate_request(request)

    def validate_request(self, request: AcquisitionRequest) -> None:
        """Provider-specific request checks. Override as needed. No I/O."""

    @abstractmethod
    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        """Execute acquisition and return artifacts for registration.

        Call validate() before this method.
        """

    def artifact_source(self, request: AcquisitionRequest) -> str:
        """Default source label used when mapping artifacts to assets."""
        return request.provider_id


class AcquisitionProviderCatalog:
    """In-process provider lookup. Does not call external services."""

    def __init__(self) -> None:
        self._providers: dict[str, AcquisitionProvider] = {}

    def add(self, provider: AcquisitionProvider) -> None:
        provider_id = provider.identity.id
        if provider_id in self._providers:
            raise AcquisitionError(f"provider '{provider_id}' is already registered")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> AcquisitionProvider:
        try:
            return self._providers[provider_id]
        except KeyError as error:
            raise AcquisitionError(f"unknown acquisition provider '{provider_id}'") from error

    def list_identities(self) -> list[ProviderIdentity]:
        return [provider.identity for provider in self._providers.values()]

    def resolve(self, request: AcquisitionRequest) -> AcquisitionProvider:
        provider = self.get(request.provider_id)
        provider.validate(request)
        return provider


def require_scope(request: AcquisitionRequest) -> None:
    """Shared check that a request includes AOI and time window."""
    if request.area_of_interest is None:
        raise AcquisitionValidationError("area_of_interest is required")
    if request.time_window is None:
        raise AcquisitionValidationError("time_window is required")


def ensure_artifact_type(artifact: AcquiredArtifact, expected: AssetType) -> None:
    if artifact.asset_type != expected:
        raise AcquisitionValidationError(
            f"artifact type '{artifact.asset_type.value}' does not match "
            f"expected '{expected.value}'"
        )
