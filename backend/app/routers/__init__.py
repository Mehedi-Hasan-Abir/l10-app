"""HTTP routers. `api_router` is mounted under `/api`; the WebSocket router (`ws.router`) is mounted by
`create_app` at the root, because the edge proxy sends `/ws/*` straight to the API."""

from fastapi import APIRouter

from app.routers import audit, auth, issues, meetings, rocks, scorecard, teams, todos
from app.routers.common import operation_id

api_router = APIRouter(prefix="/api", generate_unique_id_function=operation_id)
api_router.include_router(auth.router)
api_router.include_router(teams.router)
api_router.include_router(scorecard.router)
api_router.include_router(issues.router)
api_router.include_router(todos.router)
api_router.include_router(rocks.router)
api_router.include_router(meetings.team_router)
api_router.include_router(meetings.router)
api_router.include_router(audit.router)
