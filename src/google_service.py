"""Select one explicit HumanWire service role from the shared image."""

from __future__ import annotations

import os

role = os.environ.get("HUMANWIRE_SERVICE_ROLE", "").strip()
if role == "web":
    from google_web_index import app
elif role == "worker":
    from google_worker_index import app
else:
    raise RuntimeError("service_role_invalid") from None

__all__ = ["app"]
