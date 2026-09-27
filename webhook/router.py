import logging
from fastapi import APIRouter

from .schemas.meta_schema import IncomingMessage

from .config import get_provider

provider = get_provider()
router = APIRouter(
    prefix="/reply",
    tags=["reply"]
)