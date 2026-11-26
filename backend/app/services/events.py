"""Realtime notifications for data changes. Always call these AFTER the transaction committed."""

from app.schemas import Resource
from app.services.common import Ctx


async def invalidate(ctx: Ctx, team_id: str, *resources: Resource) -> None:
    """Tell every client of the team which cached resources are stale."""
    await ctx.hub.publish(team_id, {"type": "invalidate", "resources": list(resources)})
