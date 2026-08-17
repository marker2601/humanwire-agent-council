import pytest

from humanwire.cloud_store import FirestoreRunRepository
from humanwire.cloud_web import build_google_web_app, create_cloud_web_app
from humanwire.google_submission_app import create_google_submission_app


def test_cloud_web_exposes_only_the_canonical_durable_app_factory() -> None:
    assert create_cloud_web_app is create_google_submission_app


class FakePublisher:
    def __init__(self) -> None:
        self.topic_calls = []

    def topic_path(self, project: str, topic: str) -> str:
        self.topic_calls.append((project, topic))
        return f"projects/{project}/topics/{topic}"

    def publish(self, *_args, **_kwargs):
        raise AssertionError("building the web app must not publish")


def test_entrypoint_builder_reads_only_safe_web_configuration() -> None:
    firestore_client = object()
    publisher = FakePublisher()
    requested = []
    application = build_google_web_app(
        {
            "GOOGLE_CLOUD_PROJECT": "humanwire-demo",
            "HUMANWIRE_FIRESTORE_DATABASE": "(default)",
            "HUMANWIRE_PUBSUB_TOPIC": "humanwire-runs",
            "HUMANWIRE_PUBLIC_ORIGINS": "https://humanwire.example.test",
            "GEMINI_API_KEY": "PRIVATE-MUST-NOT-BE-READ",
        },
        firestore_client_factory=lambda **kwargs: (
            requested.append(kwargs) or firestore_client
        ),
        publisher_factory=lambda: publisher,
    )

    assert isinstance(application.state.repository, FirestoreRunRepository)
    assert application.state.dispatcher._publisher is publisher
    assert requested == [
        {"project": "humanwire-demo", "database": "(default)"}
    ]
    assert publisher.topic_calls == [("humanwire-demo", "humanwire-runs")]
    assert "PRIVATE" not in repr(application.state.__dict__)


def test_entrypoint_never_reads_model_credentials_from_the_web_environment() -> None:
    class TrackingEnvironment(dict):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.reads = []

        def get(self, key, default=None):
            self.reads.append(key)
            return super().get(key, default)

    environment = TrackingEnvironment(
        GOOGLE_CLOUD_PROJECT="humanwire-demo",
        HUMANWIRE_PUBSUB_TOPIC="humanwire-runs",
        HUMANWIRE_PUBLIC_ORIGINS="https://humanwire.example.test",
        GEMINI_API_KEY="PRIVATE-MUST-NOT-BE-READ",
    )
    build_google_web_app(
        environment,
        firestore_client_factory=lambda **_kwargs: object(),
        publisher_factory=FakePublisher,
    )

    assert set(environment.reads) == {
        "GOOGLE_CLOUD_PROJECT",
        "HUMANWIRE_FIRESTORE_DATABASE",
        "HUMANWIRE_PUBSUB_TOPIC",
        "HUMANWIRE_PUBLIC_ORIGINS",
    }
    assert "GEMINI_API_KEY" not in environment.reads


def test_provider_startup_failures_have_a_fixed_empty_exception_graph() -> None:
    class PrivatePublisher:
        def topic_path(self, _project, _topic):
            raise RuntimeError("PRIVATE-PROVIDER-CREDENTIAL/PATH")

    with pytest.raises(RuntimeError, match="^cloud_web_unavailable$") as captured:
        build_google_web_app(
            {
                "GOOGLE_CLOUD_PROJECT": "humanwire-demo",
                "HUMANWIRE_PUBSUB_TOPIC": "humanwire-runs",
                "HUMANWIRE_PUBLIC_ORIGINS": "https://humanwire.example.test",
            },
            firestore_client_factory=lambda **_kwargs: object(),
            publisher_factory=PrivatePublisher,
        )

    assert captured.value.__context__ is None
    assert captured.value.__cause__ is None
    assert "PRIVATE" not in repr(captured.value)


@pytest.mark.parametrize(
    "environment",
    (
        {},
        {
            "GOOGLE_CLOUD_PROJECT": "humanwire-demo",
            "HUMANWIRE_PUBSUB_TOPIC": "humanwire-runs",
            "HUMANWIRE_PUBLIC_ORIGINS": "http://humanwire.example.test",
        },
        {
            "GOOGLE_CLOUD_PROJECT": "humanwire-demo",
            "HUMANWIRE_PUBSUB_TOPIC": "bad/topic",
            "HUMANWIRE_PUBLIC_ORIGINS": "https://humanwire.example.test",
        },
    ),
)
def test_entrypoint_builder_fails_closed_before_client_creation(environment) -> None:
    calls = []

    with pytest.raises(ValueError, match="cloud_web_configuration_invalid"):
        build_google_web_app(
            environment,
            firestore_client_factory=lambda **kwargs: calls.append(kwargs),
            publisher_factory=lambda: calls.append("publisher"),
        )

    assert calls == []
