"""Authenticated dictionary endpoints shared by web and Tauri clients."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response

from auth_service import current_identity
from dictionary_service import (
    DictionaryNotFound,
    DictionaryUnavailable,
    DictionaryValidationError,
    PronunciationNotFound,
    dictionary_service,
)


router = APIRouter(prefix="/api/v1/dictionary", tags=["dictionary"])


@router.get("/lookup")
def lookup_dictionary(
    request: Request,
    q: str = Query(...),
    source: str = Query("en"),
) -> dict:
    current_identity(request)
    try:
        return dictionary_service.lookup(q, source)
    except DictionaryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DictionaryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DictionaryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/pronunciation")
def dictionary_pronunciation(
    request: Request,
    q: str = Query(...),
    variant: int = Query(0, ge=0, le=20),
) -> Response:
    current_identity(request)
    try:
        content, content_type = dictionary_service.pronunciation(q, variant)
    except DictionaryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PronunciationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DictionaryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )
