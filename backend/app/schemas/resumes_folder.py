from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ResumesFolderError(BaseModel):
    message: str
    at: datetime


class ResumesFolderSettings(BaseModel):
    available: bool
    reason: Literal["not_here", "always_on_copy"] | None
    enabled: bool
    folder: str | None
    folder_display: str | None
    default_folder: str | None
    custom: bool
    platform: Literal["macos", "windows", "linux"]
    picker: bool
    last_error: ResumesFolderError | None


class ResumesFolderUpdate(BaseModel):
    """Absent = unchanged. folder: null = back to the default."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    folder: str | None = None


class ShownCopy(BaseModel):
    path: str


class ChosenFolder(BaseModel):
    """What the folder dialog returned; null = the user cancelled."""
    folder: str | None
