"""Cloud Run entrypoint for the private HumanWire worker service."""

from __future__ import annotations

import os

from humanwire.cloud_worker_app import build_google_worker_app

app = build_google_worker_app(os.environ)
