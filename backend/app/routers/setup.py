"""Read-only onboarding state, connected-agent values and bundle download."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.schemas.connect import ConnectInfo
from app.schemas.setup_status import SetupStatus
from app.services import connect, setup_status

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("/status", response_model=SetupStatus)
def get_status(db: Annotated[Session, Depends(get_db)]):
    return setup_status.build_status(db)


@router.get("/connect", response_model=ConnectInfo)
def get_connect(profile: str = "full"):
    try:
        return connect.build(profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/connect/bundle")
def get_connect_bundle():
    path = settings.mcpb_bundle_path
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "The Claude Desktop bundle (mcpb/maestro-career-studio.mcpb) "
                "is not available on this install."
            ),
        )
    return FileResponse(
        path, media_type="application/zip", filename="maestro-career-studio.mcpb",
    )
