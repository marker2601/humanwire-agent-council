"""Canonical cloud-web construction for the durable public application."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from humanwire.cloud_dispatch import PubSubRunDispatcher
from humanwire.cloud_store import FirestoreRunRepository
from humanwire.google_submission_app import create_google_submission_app

create_cloud_web_app = create_google_submission_app

_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,62}$")
_TOPIC = re.compile(r"^[A-Za-z][A-Za-z0-9._~-]{2,254}$")
_DATABASE = re.compile(r"^(?:\(default\)|[A-Za-z][A-Za-z0-9._~-]{0,62})$")
_ACTION_TOKEN = "humanwire-google-cloud-action-v1"


def _configuration(
    environment: Mapping[str, str],
) -> tuple[str, str, str, frozenset[str]]:
    try:
        project = environment.get("GOOGLE_CLOUD_PROJECT", "").strip()
        database = environment.get("HUMANWIRE_FIRESTORE_DATABASE", "(default)").strip()
        topic = environment.get("HUMANWIRE_PUBSUB_TOPIC", "").strip()
        origins = tuple(
            item.strip() for item in environment.get("HUMANWIRE_PUBLIC_ORIGINS", "").split(",")
        )
        if (
            _PROJECT.fullmatch(project) is None
            or _DATABASE.fullmatch(database) is None
            or _TOPIC.fullmatch(topic) is None
            or not origins
            or any(not item for item in origins)
        ):
            raise ValueError
        hosts: set[str] = set()
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "https"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.hostname is None
                or origin != f"https://{parsed.hostname}"
            ):
                raise ValueError
            hosts.add(parsed.hostname)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("cloud_web_configuration_invalid") from None
    return project, database, topic, frozenset(hosts)


def build_google_web_app(
    environment: Mapping[str, str],
    *,
    firestore_client_factory: Callable[..., Any] | None = None,
    publisher_factory: Callable[[], Any] | None = None,
):
    """Construct provider adapters from a minimal secret-free web projection."""
    project, database, topic, hosts = _configuration(environment)
    if firestore_client_factory is None:
        from google.cloud import firestore

        firestore_client_factory = firestore.Client
    if publisher_factory is None:
        from google.cloud import pubsub_v1

        publisher_factory = pubsub_v1.PublisherClient
    failed = False
    firestore_client = None
    publisher = None
    topic_path = None
    try:
        firestore_client = firestore_client_factory(project=project, database=database)
        publisher = publisher_factory()
        topic_path = publisher.topic_path(project, topic)
    except Exception:  # noqa: BLE001 - startup must not retain provider details
        failed = True
    if failed or firestore_client is None or publisher is None or topic_path is None:
        raise RuntimeError("cloud_web_unavailable") from None
    repository = FirestoreRunRepository(firestore_client)
    dispatcher = PubSubRunDispatcher(
        publisher,
        topic_path=topic_path,
    )
    return create_google_submission_app(
        repository,
        dispatcher,
        action_token=_ACTION_TOKEN,
        allowed_hosts=hosts,
    )


__all__ = [
    "build_google_web_app",
    "create_cloud_web_app",
    "create_google_submission_app",
]
