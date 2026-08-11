import json
from datetime import UTC, datetime

import pytest

from secondsignal.domain import Channel, IncomingMessage
from secondsignal.identities import (
    AmbiguousIdentityError,
    IdentityRegistry,
    UnknownIdentityError,
    normalize_address,
)


@pytest.fixture
def registry_document() -> dict:
    return {
        "authorized_reporters": {
            "telegram": ["Reporter-TG"],
            "email": ["reporter@example.com"],
        },
        "identities": [
            {
                "identity_id": "asha-rao",
                "display_name": "Asha Rao",
                "aliases": ["Asha", "Asha Rao", "CEO"],
                "routes": [
                    {
                        "channel": "email",
                        "sender_address": "asha@example.com",
                        "recipient": "asha@example.com",
                        "conversation_id": None,
                    },
                    {
                        "channel": "telegram",
                        "sender_address": "Verifier-TG",
                        "recipient": None,
                        "conversation_id": "conv_asha_telegram",
                    },
                ],
            }
        ],
    }


@pytest.fixture
def registry(tmp_path, registry_document) -> IdentityRegistry:
    path = tmp_path / "identities.json"
    path.write_text(json.dumps(registry_document), encoding="utf-8")
    return IdentityRegistry.load(path)


def make_message(channel: Channel, sender: str) -> IncomingMessage:
    return IncomingMessage(
        message_id="msg-1",
        conversation_id="conv-1",
        connection_id="conn-1",
        channel=channel,
        sender_address=sender,
        text="hello",
        received_at=datetime(2026, 8, 10, tzinfo=UTC),
    )


def test_resolves_alias_case_insensitively(registry: IdentityRegistry) -> None:
    assert registry.resolve("ceo").display_name == "Asha Rao"


def test_rejects_ambiguous_alias(tmp_path, registry_document: dict) -> None:
    duplicate = registry_document["identities"][0].copy()
    duplicate["identity_id"] = "asha-singh"
    duplicate["display_name"] = "Asha Singh"
    duplicate["aliases"] = ["Asha"]
    registry_document["identities"].append(duplicate)
    path = tmp_path / "ambiguous.json"
    path.write_text(json.dumps(registry_document), encoding="utf-8")

    with pytest.raises(AmbiguousIdentityError):
        IdentityRegistry.load(path).resolve("Asha")


def test_rejects_unknown_identity(registry: IdentityRegistry) -> None:
    with pytest.raises(UnknownIdentityError):
        registry.resolve("Nobody")


def test_selects_email_for_telegram_origin(registry: IdentityRegistry) -> None:
    identity = registry.resolve("Asha Rao")
    route = registry.select_independent_route(identity, Channel.TELEGRAM)
    assert route is not None
    assert route.channel is Channel.EMAIL
    assert route.recipient == "asha@example.com"


def test_selects_existing_telegram_conversation_for_email_origin(
    registry: IdentityRegistry,
) -> None:
    identity = registry.resolve("Asha Rao")
    route = registry.select_independent_route(identity, Channel.EMAIL)
    assert route is not None
    assert route.channel is Channel.TELEGRAM
    assert route.conversation_id == "conv_asha_telegram"


def test_never_returns_same_channel(registry: IdentityRegistry) -> None:
    identity = registry.resolve("Asha Rao")
    route = registry.select_independent_route(identity, Channel.TELEGRAM)
    assert route is not None
    assert route.channel is not Channel.TELEGRAM


def test_authorized_reporters_are_compared_after_normalization(
    registry: IdentityRegistry,
) -> None:
    assert registry.is_authorized(make_message(Channel.EMAIL, " Reporter@Example.COM "))
    assert registry.is_authorized(make_message(Channel.TELEGRAM, "reporter-tg"))
    assert not registry.is_authorized(make_message(Channel.TELEGRAM, "intruder"))


def test_address_normalization_does_not_guess_telegram_identifier_format() -> None:
    assert normalize_address(Channel.TELEGRAM, " @Verifier_Name ") == "@verifier_name"
