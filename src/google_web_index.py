"""Cloud Run entrypoint for the durable public HumanWire web service."""

from __future__ import annotations

import os

from humanwire.cloud_web import build_google_web_app

app = build_google_web_app(os.environ)
