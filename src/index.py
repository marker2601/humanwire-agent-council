"""Vercel entrypoint for the safe, read-only HumanWire demo."""

from humanwire.demo import create_demo_app

app = create_demo_app()
