"""Vercel entrypoint for the safe, read-only SecondSignal demo."""

from secondsignal.demo import create_demo_app

app = create_demo_app()
