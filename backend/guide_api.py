"""Admin and public APIs for the MinIO-backed user guide module."""

from __future__ import annotations

import re
import json
import unicodedata
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import bleach
from bleach.css_sanitizer import CSSSanitizer
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from auth_service import require_admin
from config import settings
from database import session_scope
from models import AuditLog, Guide, GuideCategory, GuideMedia, User, utcnow
from object_storage import storage


router = APIRouter(prefix="/api/v1", tags=["guides"])
GUIDE_STATUSES = {"DRAFT", "PUBLISHED", "HIDDEN"}
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
VIDEO_MIMES = {"video/mp4", "video/webm", "video/quicktime"}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]
ALLOWED_TAGS = [
    "p", "br", "strong", "b", "em", "i", "u", "s", "sub", "sup",
    "h1", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "pre", "code",
    "hr", "a", "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "figure", "figcaption", "img", "video", "source", "iframe", "span", "div", "mark",
]
ALLOWED_ATTRIBUTES = {
    "*": ["class", "style", "id"],
    "a": ["href", "title", "target", "rel"],
    "img": [
        "src", "alt", "title", "width", "height", "data-object-key",
        "data-bucket", "data-width", "data-align",
    ],
    "video": [
        "src", "controls", "poster", "preload", "width", "height",
        "data-object-key", "data-bucket",
    ],
    "source": ["src", "type"],
    "iframe": [
        "src", "title", "width", "height", "allow", "allowfullscreen",
        "loading", "referrerpolicy",
    ],
    "th": ["colspan", "rowspan", "scope"],
    "td": ["colspan", "rowspan"],
}
CSS_SANITIZER = CSSSanitizer(
    allowed_css_properties=[
        "color", "background-color", "font-family", "font-size", "font-weight",
        "font-style", "text-align", "text-decoration", "width", "max-width",
        "height", "margin-left", "margin-right", "display", "float",
    ]
)


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", ascii_value)).strip("-")


def _safe_name(value: str) -> str:
    stem = _slugify(Path(value).stem)[:100] or "media"
    suffix = Path(value).suffix.lower()
    return f"{stem}{suffix}"


def _valid_embed_url(url: str, *, iframe: bool = False) -> bool:
    if url.startswith("/api/v1/guide-media/"):
        return not iframe
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if iframe:
        return parsed.hostname in {
            "youtube.com", "www.youtube.com", "youtube-nocookie.com",
            "www.youtube-nocookie.com", "youtu.be",
        }
    return True


def sanitize_guide_html(value: str) -> str:
    if re.search(r"(?:src|href)\s*=\s*['\"]\s*data:", value, re.IGNORECASE):
        raise HTTPException(status_code=422, detail="Không được lưu media dạng Base64")
    cleaned = bleach.clean(
        value,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        css_sanitizer=CSS_SANITIZER,
        strip=True,
        strip_comments=True,
    )

    def validate_src(match: re.Match[str]) -> str:
        tag, before, src, after = match.group(1), match.group(2), match.group(3), match.group(4)
        if not _valid_embed_url(src, iframe=tag.lower() == "iframe"):
            return ""
        return f"<{tag}{before}src=\"{bleach.clean(src, strip=True)}\"{after}>"

    return re.sub(
        r"<(img|video|source|iframe)([^>]*?)src=[\"']([^\"']+)[\"']([^>]*)>",
        validate_src,
        cleaned,
        flags=re.IGNORECASE,
    )


class GuideWrite(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    slug: str = Field(default="", max_length=280)
    summary: str = Field(default="", max_length=2000)
    thumbnail_url: str | None = Field(default=None, max_length=2048)
    thumbnail_object_key: str | None = Field(default=None, max_length=1024)
    category_id: str | None = Field(default=None, max_length=36)
    content: dict[str, Any] = Field(default_factory=dict)
    rendered_html: str = Field(default="", max_length=2_000_000)
    sort_order: int = Field(default=0, ge=-100000, le=100000)
    status: Literal["DRAFT", "PUBLISHED", "HIDDEN"] = "DRAFT"
    keywords: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip()[:80] for value in values if value.strip()))

    @field_validator("thumbnail_url")
    @classmethod
    def validate_thumbnail_url(cls, value: str | None) -> str | None:
        if value and not _valid_embed_url(value):
            raise ValueError("URL ảnh đại diện không hợp lệ")
        return value

    @model_validator(mode="after")
    def reject_embedded_data(self):
        if re.search(r"data\s*:", json.dumps(self.content), re.IGNORECASE):
            raise ValueError("Không được lưu media Base64 trong nội dung")
        return self


class CategoryWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(default="", max_length=140)
    sort_order: int = Field(default=0, ge=-100000, le=100000)


def _category_payload(category: GuideCategory) -> dict[str, Any]:
    return {
        "id": category.id,
        "name": category.name,
        "slug": category.slug,
        "sort_order": category.sort_order,
    }


def _guide_payload(session, guide: Guide, *, detail: bool = False) -> dict[str, Any]:
    category = session.get(GuideCategory, guide.category_id) if guide.category_id else None
    creator = session.get(User, guide.created_by) if guide.created_by else None
    payload: dict[str, Any] = {
        "id": guide.id,
        "title": guide.title,
        "slug": guide.slug,
        "summary": guide.summary,
        "thumbnail_url": guide.thumbnail_url,
        "thumbnail_object_key": guide.thumbnail_object_key,
        "category_id": guide.category_id,
        "category": _category_payload(category) if category else None,
        "status": guide.status,
        "sort_order": guide.sort_order,
        "keywords": guide.keywords or [],
        "created_by": guide.created_by,
        "creator_name": creator.display_name if creator else None,
        "created_at": guide.created_at,
        "updated_at": guide.updated_at,
        "published_at": guide.published_at,
    }
    if detail:
        payload.update(
            content=guide.content or {},
            rendered_html=guide.rendered_html,
            content_format=guide.content_format,
        )
    return payload


def _find_category(session, category_id: str | None) -> None:
    if category_id and session.get(GuideCategory, category_id) is None:
        raise HTTPException(status_code=422, detail="Danh mục không tồn tại")


def _unique_slug(session, value: str, title: str, exclude_id: str | None = None) -> str:
    slug = _slugify(value or title)
    if not slug:
        raise HTTPException(status_code=422, detail="Slug không hợp lệ")
    statement = select(Guide.id).where(Guide.slug == slug)
    if exclude_id:
        statement = statement.where(Guide.id != exclude_id)
    if session.scalar(statement):
        raise HTTPException(status_code=409, detail="Slug đã được sử dụng")
    return slug


def _apply_write(session, guide: Guide, body: GuideWrite, *, exclude_id: str | None = None) -> None:
    _find_category(session, body.category_id)
    guide.title = body.title.strip()
    guide.slug = _unique_slug(session, body.slug, guide.title, exclude_id)
    guide.summary = body.summary.strip()
    guide.thumbnail_url = body.thumbnail_url
    guide.thumbnail_object_key = body.thumbnail_object_key
    guide.category_id = body.category_id
    guide.content = body.content
    guide.rendered_html = sanitize_guide_html(body.rendered_html)
    if body.status == "PUBLISHED" and not guide.rendered_html.strip():
        raise HTTPException(status_code=422, detail="Không thể đăng bài chưa có nội dung")
    guide.content_format = "tiptap-json"
    guide.status = body.status
    guide.sort_order = body.sort_order
    guide.keywords = body.keywords
    guide.search_text = " ".join(
        [
            guide.title,
            guide.summary,
            *body.keywords,
            bleach.clean(guide.rendered_html, tags=[], strip=True),
        ]
    )
    guide.updated_at = utcnow()
    if body.status == "PUBLISHED" and guide.published_at is None:
        guide.published_at = utcnow()


def _audit(session, identity: dict[str, Any], action: str, target_id: str) -> None:
    session.add(
        AuditLog(
            actor_user_id=identity["user_id"],
            action=action,
            target_type="guide",
            target_id=target_id,
        )
    )


@router.get("/guide-categories")
def public_categories() -> dict[str, Any]:
    with session_scope() as session:
        rows = session.scalars(
            select(GuideCategory).order_by(GuideCategory.sort_order, GuideCategory.name)
        ).all()
        return {"items": [_category_payload(row) for row in rows]}


@router.get("/guides")
def public_guides(
    q: str = Query("", max_length=200),
    category: str = Query("", max_length=140),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
) -> dict[str, Any]:
    with session_scope() as session:
        conditions = [Guide.status == "PUBLISHED"]
        if q.strip():
            term = f"%{q.strip()}%"
            conditions.append(
                or_(
                    Guide.title.ilike(term),
                    Guide.summary.ilike(term),
                    Guide.search_text.ilike(term),
                )
            )
        if category:
            category_id = session.scalar(
                select(GuideCategory.id).where(GuideCategory.slug == category)
            )
            conditions.append(Guide.category_id == (category_id or "__missing__"))
        total = session.scalar(select(func.count(Guide.id)).where(*conditions)) or 0
        rows = session.scalars(
            select(Guide)
            .where(*conditions)
            .order_by(Guide.sort_order.asc(), Guide.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {
            "items": [_guide_payload(session, row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }


@router.get("/guides/search")
def search_guides(
    q: str = Query(..., min_length=1, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
) -> dict[str, Any]:
    return public_guides(q=q, category="", page=page, page_size=page_size)


@router.get("/guides/{slug}")
def public_guide(slug: str) -> dict[str, Any]:
    with session_scope() as session:
        guide = session.scalar(
            select(Guide).where(Guide.slug == slug, Guide.status == "PUBLISHED")
        )
        if guide is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài hướng dẫn")
        rows = session.scalars(
            select(Guide)
            .where(Guide.status == "PUBLISHED")
            .order_by(Guide.sort_order.asc(), Guide.updated_at.desc())
        ).all()
        index = next((i for i, row in enumerate(rows) if row.id == guide.id), -1)
        payload = _guide_payload(session, guide, detail=True)
        payload["previous"] = _guide_payload(session, rows[index - 1]) if index > 0 else None
        payload["next"] = _guide_payload(session, rows[index + 1]) if 0 <= index < len(rows) - 1 else None
        return payload


@router.get("/admin/guides")
def admin_guides(
    request: Request,
    q: str = Query("", max_length=200),
    status: str = Query("", max_length=20),
    category_id: str = Query("", max_length=36),
    sort: Literal["created_desc", "created_asc", "order_asc", "order_desc"] = "order_asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    require_admin(request)
    with session_scope() as session:
        conditions = []
        if q.strip():
            conditions.append(Guide.title.ilike(f"%{q.strip()}%"))
        if status:
            if status not in GUIDE_STATUSES:
                raise HTTPException(status_code=422, detail="Trạng thái không hợp lệ")
            conditions.append(Guide.status == status)
        if category_id:
            conditions.append(Guide.category_id == category_id)
        order = {
            "created_desc": Guide.created_at.desc(),
            "created_asc": Guide.created_at.asc(),
            "order_asc": Guide.sort_order.asc(),
            "order_desc": Guide.sort_order.desc(),
        }[sort]
        total = session.scalar(select(func.count(Guide.id)).where(*conditions)) or 0
        rows = session.scalars(
            select(Guide)
            .where(*conditions)
            .order_by(order, Guide.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {
            "items": [_guide_payload(session, row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }


@router.get("/admin/guides/{guide_id}")
def admin_guide(guide_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    with session_scope() as session:
        guide = session.get(Guide, guide_id)
        if guide is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài hướng dẫn")
        return _guide_payload(session, guide, detail=True)


@router.post("/admin/guides", status_code=201)
def create_guide(body: GuideWrite, request: Request) -> dict[str, Any]:
    identity = require_admin(request)
    with session_scope() as session:
        guide = Guide(created_by=identity["user_id"])
        _apply_write(session, guide, body)
        session.add(guide)
        session.flush()
        _audit(session, identity, "guide.create", guide.id)
        return _guide_payload(session, guide, detail=True)


@router.put("/admin/guides/{guide_id}")
def update_guide(guide_id: str, body: GuideWrite, request: Request) -> dict[str, Any]:
    identity = require_admin(request)
    with session_scope() as session:
        guide = session.get(Guide, guide_id)
        if guide is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài hướng dẫn")
        _apply_write(session, guide, body, exclude_id=guide.id)
        session.flush()
        _audit(session, identity, "guide.update", guide.id)
        return _guide_payload(session, guide, detail=True)


@router.delete("/admin/guides/{guide_id}")
def delete_guide(guide_id: str, request: Request) -> dict[str, bool]:
    identity = require_admin(request)
    with session_scope() as session:
        guide = session.get(Guide, guide_id)
        if guide is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài hướng dẫn")
        _audit(session, identity, "guide.delete", guide.id)
        session.delete(guide)
        return {"ok": True}


def _change_status(guide_id: str, status: str, request: Request) -> dict[str, Any]:
    identity = require_admin(request)
    with session_scope() as session:
        guide = session.get(Guide, guide_id)
        if guide is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài hướng dẫn")
        if status == "PUBLISHED" and not guide.rendered_html.strip():
            raise HTTPException(status_code=422, detail="Không thể đăng bài chưa có nội dung")
        guide.status = status
        guide.updated_at = utcnow()
        if status == "PUBLISHED":
            guide.published_at = utcnow()
        _audit(session, identity, f"guide.{status.lower()}", guide.id)
        session.flush()
        return _guide_payload(session, guide, detail=True)


@router.post("/admin/guides/{guide_id}/publish")
def publish_guide(guide_id: str, request: Request) -> dict[str, Any]:
    return _change_status(guide_id, "PUBLISHED", request)


@router.post("/admin/guides/{guide_id}/unpublish")
def unpublish_guide(guide_id: str, request: Request) -> dict[str, Any]:
    return _change_status(guide_id, "HIDDEN", request)


@router.post("/admin/guide-categories", status_code=201)
def create_category(body: CategoryWrite, request: Request) -> dict[str, Any]:
    require_admin(request)
    slug = _slugify(body.slug or body.name)
    if not slug:
        raise HTTPException(status_code=422, detail="Slug danh mục không hợp lệ")
    try:
        with session_scope() as session:
            category = GuideCategory(
                name=body.name.strip(), slug=slug, sort_order=body.sort_order
            )
            session.add(category)
            session.flush()
            return _category_payload(category)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Danh mục đã tồn tại") from exc


def _detected_media(payload: bytes, claimed: str) -> tuple[str, str, int | None, int | None]:
    try:
        image = Image.open(BytesIO(payload))
        image.verify()
        mime = Image.MIME.get(image.format or "", "")
        if mime not in IMAGE_MIMES:
            raise HTTPException(status_code=415, detail="Định dạng ảnh không được hỗ trợ")
        reopened = Image.open(BytesIO(payload))
        width, height = reopened.width, reopened.height
        reopened.close()
        if width * height > 50_000_000:
            raise HTTPException(status_code=413, detail="Ảnh có kích thước điểm ảnh quá lớn")
        return "image", mime, width, height
    except Image.DecompressionBombError as exc:
        raise HTTPException(status_code=413, detail="Ảnh có kích thước điểm ảnh quá lớn") from exc
    except (UnidentifiedImageError, OSError):
        pass
    if len(payload) >= 12 and payload[4:8] == b"ftyp":
        mime = "video/quicktime" if b"qt  " in payload[8:16] else "video/mp4"
    elif payload.startswith(b"\x1aE\xdf\xa3"):
        mime = "video/webm"
    else:
        raise HTTPException(status_code=415, detail="Nội dung file media không hợp lệ")
    if claimed and claimed not in VIDEO_MIMES:
        raise HTTPException(status_code=415, detail="MIME type video không hợp lệ")
    return "video", mime, None, None


@router.post("/admin/guide-media/upload", status_code=201)
async def upload_guide_media(
    request: Request, file: UploadFile = File(...)
) -> dict[str, Any]:
    identity = require_admin(request)
    if storage is None:
        raise HTTPException(status_code=503, detail="MinIO chưa được cấu hình")
    claimed = (file.content_type or "").lower()
    max_bytes = (
        settings.guide_video_max_bytes
        if claimed in VIDEO_MIMES
        else settings.guide_image_max_bytes
    )
    payload = await file.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise HTTPException(status_code=413, detail="File vượt quá giới hạn dung lượng")
    if not payload:
        raise HTTPException(status_code=422, detail="File rỗng")
    media_type, mime_type, width, height = _detected_media(payload, claimed)
    actual_limit = (
        settings.guide_image_max_bytes
        if media_type == "image"
        else settings.guide_video_max_bytes
    )
    if len(payload) > actual_limit:
        raise HTTPException(status_code=413, detail="File vượt quá giới hạn dung lượng")
    now = datetime.now(timezone.utc)
    safe_name = _safe_name(file.filename or "media")
    object_key = f"guides/{now:%Y}/{now:%m}/{uuid.uuid4()}-{safe_name}"
    media_id = str(uuid.uuid4())
    url = (
        f"{settings.minio_public_url}/{settings.minio_bucket_guides}/{object_key}"
        if settings.minio_public_url
        else f"/api/v1/guide-media/{media_id}/content"
    )
    storage.put_bytes(settings.minio_bucket_guides, object_key, payload, mime_type)
    try:
        with session_scope() as session:
            media = GuideMedia(
                id=media_id,
                file_name=safe_name,
                original_name=Path(file.filename or "media").name[:512],
                object_key=object_key,
                bucket=settings.minio_bucket_guides,
                url=url,
                mime_type=mime_type,
                media_type=media_type,
                size=len(payload),
                width=width,
                height=height,
                uploaded_by=identity["user_id"],
            )
            session.add(media)
            session.flush()
            return _media_payload(media)
    except Exception:
        storage.remove_object(settings.minio_bucket_guides, object_key)
        raise


def _media_payload(media: GuideMedia) -> dict[str, Any]:
    return {
        "id": media.id,
        "file_name": media.file_name,
        "original_name": media.original_name,
        "object_key": media.object_key,
        "bucket": media.bucket,
        "url": media.url,
        "mime_type": media.mime_type,
        "media_type": media.media_type,
        "size": media.size,
        "width": media.width,
        "height": media.height,
        "created_at": media.created_at,
    }


@router.get("/admin/guide-media")
def list_guide_media(
    request: Request,
    q: str = Query("", max_length=200),
    media_type: Literal["", "image", "video"] = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
) -> dict[str, Any]:
    require_admin(request)
    with session_scope() as session:
        conditions = []
        if q:
            conditions.append(GuideMedia.original_name.ilike(f"%{q}%"))
        if media_type:
            conditions.append(GuideMedia.media_type == media_type)
        total = session.scalar(select(func.count(GuideMedia.id)).where(*conditions)) or 0
        rows = session.scalars(
            select(GuideMedia)
            .where(*conditions)
            .order_by(GuideMedia.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {"items": [_media_payload(row) for row in rows], "total": total, "page": page}


@router.delete("/admin/guide-media/{media_id}")
def delete_guide_media(media_id: str, request: Request) -> dict[str, bool]:
    require_admin(request)
    with session_scope() as session:
        media = session.get(GuideMedia, media_id)
        if media is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy media")
        used = session.scalar(
            select(Guide.id).where(
                or_(
                    Guide.thumbnail_object_key == media.object_key,
                    Guide.rendered_html.ilike(f"%{media.object_key}%"),
                    Guide.rendered_html.ilike(f"%{media.url}%"),
                )
            ).limit(1)
        )
        if used:
            raise HTTPException(status_code=409, detail="File đang được sử dụng trong bài hướng dẫn")
        bucket, key = media.bucket, media.object_key
        session.delete(media)
    if storage is not None:
        storage.remove_object(bucket, key)
    return {"ok": True}


@router.get("/guide-media/{media_id}/content")
def guide_media_content(media_id: str, request: Request) -> StreamingResponse:
    if storage is None:
        raise HTTPException(status_code=503, detail="MinIO chưa được cấu hình")
    with session_scope() as session:
        media = session.get(GuideMedia, media_id)
        if media is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy media")
        bucket, object_key, mime_type, size = (
            media.bucket,
            media.object_key,
            media.mime_type,
            media.size,
        )
    range_header = request.headers.get("range", "")
    start, end = 0, max(0, size - 1)
    status_code = 200
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match:
            raise HTTPException(status_code=416, detail="Range không hợp lệ")
        if not match.group(1) and match.group(2):
            suffix_length = int(match.group(2))
            start = max(0, size - suffix_length)
        elif match.group(1):
            start = int(match.group(1))
        if match.group(1) and match.group(2):
            end = min(int(match.group(2)), end)
        if start > end or start >= size:
            raise HTTPException(status_code=416, detail="Range vượt quá kích thước file")
        status_code = 206
    length = end - start + 1
    response = storage.client.get_object(
        bucket,
        storage.safe_key(object_key),
        offset=start,
        length=length if status_code == 206 else 0,
    )

    def body():
        try:
            yield from response.stream(1024 * 1024)
        finally:
            response.close()
            response.release_conn()

    return StreamingResponse(
        body(),
        status_code=status_code,
        media_type=mime_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(length if status_code == 206 else size),
            "Cache-Control": "public, max-age=86400",
            **(
                {"Content-Range": f"bytes {start}-{end}/{size}"}
                if status_code == 206
                else {}
            ),
        },
    )
