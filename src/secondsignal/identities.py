from pathlib import Path

from pydantic import BaseModel

from secondsignal.domain import (
    Channel,
    IncomingMessage,
    VerificationRoute,
    VerifiedIdentity,
)


class UnknownIdentityError(LookupError):
    """Raised when no verified identity matches a supplied name."""


class AmbiguousIdentityError(LookupError):
    """Raised when an alias belongs to more than one verified identity."""


class RegistryDocument(BaseModel):
    authorized_reporters: dict[Channel, list[str]]
    identities: list[VerifiedIdentity]


def normalize_address(channel: Channel, value: str) -> str:
    """Normalize a transport address without guessing its provider-specific shape."""
    del channel
    return value.strip().casefold()


def normalize_alias(value: str) -> str:
    return " ".join(value.split()).casefold()


def route_is_deliverable(route: VerificationRoute) -> bool:
    if route.channel is Channel.EMAIL:
        return bool(route.recipient)
    if route.channel is Channel.TELEGRAM:
        return bool(route.conversation_id)
    return False


class IdentityRegistry:
    def __init__(self, document: RegistryDocument) -> None:
        self._document = document
        self._alias_index: dict[str, list[VerifiedIdentity]] = {}
        for identity in document.identities:
            aliases = {*identity.aliases, identity.display_name}
            for alias in aliases:
                self._alias_index.setdefault(normalize_alias(alias), []).append(identity)

        self._authorized_reporters = {
            channel: {normalize_address(channel, address) for address in addresses}
            for channel, addresses in document.authorized_reporters.items()
        }

    @classmethod
    def load(cls, path: Path) -> "IdentityRegistry":
        document = RegistryDocument.model_validate_json(path.read_text(encoding="utf-8"))
        return cls(document)

    def is_authorized(self, message: IncomingMessage) -> bool:
        allowed = self._authorized_reporters.get(message.channel, set())
        return normalize_address(message.channel, message.sender_address) in allowed

    def resolve(self, name: str) -> VerifiedIdentity:
        matches = self._alias_index.get(normalize_alias(name), [])
        if not matches:
            raise UnknownIdentityError(name)
        if len(matches) > 1:
            raise AmbiguousIdentityError(name)
        return matches[0]

    def select_independent_route(
        self,
        identity: VerifiedIdentity,
        origin: Channel,
    ) -> VerificationRoute | None:
        return next(
            (
                route
                for route in identity.routes
                if route.channel is not origin and route_is_deliverable(route)
            ),
            None,
        )
