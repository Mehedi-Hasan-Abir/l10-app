"""Realtime fan-out: an in-process hub of team WebSocket connections (PLAN section 7)."""

from app.realtime.hub import Hub

__all__ = ["Hub"]
