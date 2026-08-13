"""HTTPS synchronization API used by packaged desktop clients."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from auth_service import (
    current_identity,
    issue_access_token,
    issue_token_pair,
    sha256,
    verify_password,
)
from config import settings
from database import session_scope
from models import (
    Asset,
    Attempt,
    ClassAssignment,
    ClassMember,
    DesktopSync,
    Device,
    Exam,
    ExamSource,
    RefreshToken,
    SystemState,
    User,
    utcnow,
)
from object_storage import storage


router = APIRouter(prefix="/api/v1/desktop", tags=["desktop"])


class DesktopActivateRequest(BaseModel):
    code: str = Field(min_length=8, max_length=80)
    device_key: str = Field(min_length=16, max_length=512)
    device_name: str = Field(default="Examify", max_length=160)
    platform: str = Field(default="windows", max_length=40)
    app_version: str = ""


class DesktopRegisterRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=160)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    password_confirmation: str = Field(min_length=8, max_length=128)
    setup_token: str = Field(min_length=32, max_length=2048)


class DesktopLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    device_key: str = Field(min_length=16, max_length=512)
    device_name: str = Field(default="Examify Desktop", max_length=160)
    platform: str = Field(default="windows", max_length=40)
    app_version: str = Field(default="", max_length=40)


class DesktopIdentityUpgradeRequest(BaseModel):
    device_key: str = Field(min_length=16, max_length=512)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class SyncAsset(BaseModel):
    asset_id: str = Field(min_length=1, max_length=180)
    kind: str = Field(min_length=1, max_length=40)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=120)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SyncManifest(BaseModel):
    data_epoch: str = Field(min_length=36, max_length=64)
    client_exam_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    category: str = Field(default="", max_length=120)
    base_revision: int | None = Field(default=None, ge=1)
    payload: dict[str, Any]
    assets: list[SyncAsset] = Field(max_length=256)


def _manifest_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _completion_is_recent(sync: DesktopSync, *, lease_seconds: int = 300) -> bool:
    if sync.status != "completing":
        return False
    updated_at = sync.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return (utcnow() - updated_at).total_seconds() < lease_seconds


def _normalize_platform(value: str) -> str:
    lowered = value.strip().lower()
    if "win" in lowered:
        return "windows"
    if "linux" in lowered:
        return "linux"
    return lowered[:40] or "desktop"


def _token_response(user: User, device: Device) -> dict[str, Any]:
    access, refresh = issue_token_pair(user, device)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
        "user_id": user.id,
        "device_id": device.id,
        "role": user.role,
        "exam_limit": user.exam_limit,
        "exam_created_count": user.exam_created_count,
    }


@router.post("/activate")
def desktop_activate(
    body: DesktopActivateRequest, request: Request
) -> dict[str, Any]:
    from platform_api import ActivateRequest, redeem_activation

    payload = body.model_dump()
    payload["platform"] = _normalize_platform(payload.get("platform", ""))
    return redeem_activation(
        ActivateRequest(**payload, client_kind="desktop"),
        request=request,
        response=Response(),
    )


@router.post("/auth/register")
def desktop_register(body: DesktopRegisterRequest, request: Request) -> dict[str, Any]:
    from platform_api import RegisterRequest, register

    return register(RegisterRequest(**body.model_dump()), request, Response())


@router.post("/auth/login")
def desktop_login(body: DesktopLoginRequest) -> dict[str, Any]:
    key_hash = sha256(body.device_key)
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == body.email.strip().lower()))
        if (
            user is None
            or user.registered_at is None
            or not user.password_hash
            or not verify_password(user.password_hash, body.password)
        ):
            raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng")
        if user.status != "active":
            raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa")
        if user.role == "admin":
            device = session.scalar(
                select(Device).where(
                    Device.user_id == user.id,
                    Device.hardware_key_hash == key_hash,
                )
            )
            if device is None:
                device = Device(
                    user_id=user.id,
                    device_key_hash=key_hash,
                    hardware_key_hash=key_hash,
                    identity_kind="desktop_hardware",
                    name=body.device_name,
                    platform=_normalize_platform(body.platform),
                    app_version=body.app_version,
                )
                session.add(device)
                session.flush()
        else:
            device = session.scalar(
                select(Device).where(
                    Device.user_id == user.id,
                    Device.hardware_key_hash == key_hash,
                    Device.revoked_at.is_(None),
                )
            )
            if device is None:
                raise HTTPException(
                    status_code=403,
                    detail="Thiết bị này chưa được kích hoạt cho tài khoản",
                )
        device.platform = _normalize_platform(body.platform)
        device.app_version = body.app_version
        device.name = body.device_name
        user_id = user.id
        device_id = device.id
        device.last_seen_at = utcnow()
    # Issue the refresh token only after a newly-created admin device has
    # committed, otherwise the refresh-token FK can race the device insert.
    with session_scope() as session:
        committed_user = session.get(User, user_id)
        committed_device = session.get(Device, device_id)
        if committed_user is None or committed_device is None:
            raise HTTPException(status_code=401, detail="Không tạo được phiên đăng nhập")
        return _token_response(committed_user, committed_device)


@router.post("/auth/upgrade-device")
def desktop_upgrade_identity(
    body: DesktopIdentityUpgradeRequest, request: Request
) -> dict[str, bool]:
    identity = current_identity(request)
    key_hash = sha256(body.device_key)
    with session_scope() as session:
        device = session.get(Device, identity["device_id"])
        if not device or device.revoked_at:
            raise HTTPException(status_code=401, detail="Thiết bị đã bị thu hồi")
        device.hardware_key_hash = key_hash
        device.identity_kind = "desktop_hardware"
        device.last_seen_at = utcnow()
    return {"ok": True}


@router.post("/auth/refresh")
def desktop_refresh(body: RefreshRequest) -> dict[str, Any]:
    with session_scope() as session:
        record = session.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == sha256(body.refresh_token)
            )
        )
        if record is None or record.revoked_at is not None:
            raise HTTPException(status_code=401, detail="Refresh token không hợp lệ")
        expires_at = record.expires_at
        if expires_at <= utcnow():
            raise HTTPException(status_code=401, detail="Refresh token đã hết hạn")
        device = session.get(Device, record.device_id)
        user = session.get(User, device.user_id) if device else None
        if not device or not user or device.revoked_at or user.status != "active":
            raise HTTPException(status_code=401, detail="Thiết bị đã bị thu hồi")
        return {
            "access_token": issue_access_token(user, device),
            "token_type": "bearer",
            "expires_in": settings.access_token_minutes * 60,
            "user_id": user.id,
            "device_id": device.id,
            "role": user.role,
            "exam_limit": user.exam_limit,
            "exam_created_count": user.exam_created_count,
        }


@router.post("/auth/logout")
def desktop_logout(body: RefreshRequest) -> dict[str, bool]:
    with session_scope() as session:
        record = session.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == sha256(body.refresh_token)
            )
        )
        if record is not None and record.revoked_at is None:
            record.revoked_at = utcnow()
    return {"ok": True}


@router.post("/sync/exams")
def create_sync(body: SyncManifest, request: Request) -> dict[str, Any]:
    identity = current_identity(request)
    manifest = body.model_dump(mode="json")
    if len(json.dumps(manifest, ensure_ascii=False)) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Manifest quá lớn")
    if any(asset.size > 250 * 1024 * 1024 for asset in body.assets):
        raise HTTPException(status_code=413, detail="Asset vượt quá giới hạn 250 MB")
    manifest_hash = _manifest_hash(manifest)
    with session_scope() as session:
        state = session.get(SystemState, "data_epoch")
        if state is None or body.data_epoch != state.value:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stale_data_epoch",
                    "message": "Dữ liệu Desktop thuộc epoch cũ và phải được quarantine.",
                    "current_data_epoch": state.value if state else None,
                },
            )
        sync = session.scalar(
            select(DesktopSync).where(
                DesktopSync.user_id == identity["user_id"],
                DesktopSync.client_exam_id == body.client_exam_id,
            )
        )
        if sync is None:
            # Serialize creation per owner, then re-check. Without the second
            # read, two API workers can both pass the initial SELECT and one
            # fails the unique constraint instead of returning an idempotent
            # receipt.
            user = session.scalar(
                select(User)
                .where(User.id == identity["user_id"])
                .with_for_update()
            )
            sync = session.scalar(
                select(DesktopSync).where(
                    DesktopSync.user_id == identity["user_id"],
                    DesktopSync.client_exam_id == body.client_exam_id,
                )
            )
            if sync is None:
                existing_exam = session.scalar(
                    select(Exam).where(
                        Exam.owner_user_id == identity["user_id"],
                        Exam.client_exam_id == body.client_exam_id,
                    )
                )
                if (
                    user
                    and user.role != "admin"
                    and user.exam_limit is not None
                    and (existing_exam is None or existing_exam.deleted_at is not None)
                    and user.exam_created_count >= user.exam_limit
                ):
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            f"Đã đạt giới hạn {user.exam_limit} đề thi. "
                            "Vui lòng liên hệ quản trị viên để tăng hạn mức."
                        ),
                    )
                sync = DesktopSync(
                    user_id=identity["user_id"],
                    client_exam_id=body.client_exam_id,
                    manifest=manifest,
                    manifest_hash=manifest_hash,
                    uploaded_assets={},
                    exam_id=existing_exam.id if existing_exam else None,
                )
                session.add(sync)
                session.flush()
        if sync is not None:
            stored_hash = sync.manifest_hash or _manifest_hash(sync.manifest or {})
            existing_exam = session.get(Exam, sync.exam_id) if sync.exam_id else None
            if existing_exam is not None and existing_exam.deleted_at is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "exam_deleted",
                        "message": "Đề đã bị xóa trên web; bản local không được tải đè.",
                        "current_revision": existing_exam.content_revision,
                    },
                )
            if (
                existing_exam is not None
                and sync.status != "ready"
                and body.base_revision != existing_exam.content_revision
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "exam_revision_conflict",
                        "message": "Đề đã được chỉnh sửa trên web; bản local vẫn được giữ.",
                        "base_revision": body.base_revision,
                        "current_revision": existing_exam.content_revision,
                    },
                )
            if stored_hash != manifest_hash:
                if existing_exam is not None:
                    if body.base_revision != existing_exam.content_revision:
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": "exam_revision_conflict",
                                "message": "Đề đã được chỉnh sửa trên web; bản local vẫn được giữ.",
                                "base_revision": body.base_revision,
                                "current_revision": existing_exam.content_revision,
                            },
                        )
                if _completion_is_recent(sync):
                    raise HTTPException(
                        status_code=409,
                        detail="Phiên bản trước đang hoàn tất; hãy thử lại sau.",
                    )
                previous_assets = {
                    str(item.get("asset_id")): item
                    for item in (sync.manifest or {}).get("assets") or []
                }
                retained_uploads: dict[str, Any] = {}
                for asset in manifest.get("assets") or []:
                    asset_id = str(asset.get("asset_id"))
                    previous = previous_assets.get(asset_id) or {}
                    uploaded = (sync.uploaded_assets or {}).get(asset_id) or {}
                    if (
                        previous.get("sha256") == asset.get("sha256")
                        and previous.get("size") == asset.get("size")
                        and uploaded.get("sha256") == asset.get("sha256")
                    ):
                        retained_uploads[asset_id] = uploaded
                sync.manifest = manifest
                sync.manifest_hash = manifest_hash
                sync.uploaded_assets = retained_uploads
                sync.status = "uploading"
                sync.updated_at = utcnow()
            elif sync.manifest_hash is None:
                sync.manifest_hash = stored_hash
        receipt_exam = session.get(Exam, sync.exam_id) if sync.exam_id else None
        return {
            "sync_id": sync.id,
            "status": sync.status,
            "exam_id": sync.exam_id,
            "revision": receipt_exam.content_revision if receipt_exam else None,
            "uploaded_assets": sorted((sync.uploaded_assets or {}).keys()),
        }


def _owned_sync(sync_id: str, user_id: str) -> DesktopSync:
    with session_scope() as session:
        sync = session.get(DesktopSync, sync_id)
        if sync is None or sync.user_id != user_id:
            raise HTTPException(status_code=404, detail="Không tìm thấy phiên đồng bộ")
        session.expunge(sync)
        return sync


@router.put("/sync/exams/{sync_id}/assets/{asset_id}")
async def upload_sync_asset(
    sync_id: str,
    asset_id: str,
    request: Request,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    identity = current_identity(request)
    sync = _owned_sync(sync_id, identity["user_id"])
    expected = next(
        (
            item
            for item in (sync.manifest.get("assets") or [])
            if item.get("asset_id") == asset_id
        ),
        None,
    )
    if expected is None:
        raise HTTPException(status_code=404, detail="Asset không thuộc manifest")
    if storage is None:
        raise HTTPException(status_code=503, detail="Object storage chưa sẵn sàng")
    digest = hashlib.sha256()
    size = 0
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as output:
            temp_path = Path(output.name)
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > int(expected["size"]):
                    raise HTTPException(status_code=422, detail="Asset sai kích thước")
                digest.update(chunk)
                output.write(chunk)
        if size != int(expected["size"]) or digest.hexdigest() != expected["sha256"]:
            raise HTTPException(status_code=422, detail="Checksum asset không khớp")
        bucket = (
            settings.minio_bucket_audio
            if expected.get("kind") == "audio"
            else settings.minio_bucket_sources
            if expected.get("kind") == "source"
            else settings.minio_bucket_assets
        )
        object_key = (
            f"desktop/{identity['user_id']}/{sync.client_exam_id}/{asset_id}"
        )
        storage.put_file(bucket, object_key, temp_path, expected.get("content_type"))
        with session_scope() as session:
            row = session.get(DesktopSync, sync_id)
            uploaded = dict(row.uploaded_assets or {})
            uploaded[asset_id] = {
                "bucket": bucket,
                "object_key": object_key,
                "sha256": digest.hexdigest(),
                "size": size,
            }
            row.uploaded_assets = uploaded
            row.updated_at = utcnow()
        return {"ok": True, "asset_id": asset_id}
    finally:
        await file.close()
        if temp_path:
            temp_path.unlink(missing_ok=True)


@router.post("/sync/exams/{sync_id}/complete")
def complete_sync(sync_id: str, request: Request) -> dict[str, Any]:
    identity = current_identity(request)
    with session_scope() as session:
        row = session.scalar(
            select(DesktopSync)
            .where(
                DesktopSync.id == sync_id,
                DesktopSync.user_id == identity["user_id"],
            )
            .with_for_update()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy phiên đồng bộ")
        if row.exam_id is None:
            # Crash recovery: persist_final_exam may have committed before the
            # sync receipt stored exam_id. Reattach to server truth without
            # replaying the stale local payload over a possible later web edit.
            recovered_exam = session.scalar(
                select(Exam).where(
                    Exam.owner_user_id == identity["user_id"],
                    Exam.client_exam_id == row.client_exam_id,
                )
            )
            if recovered_exam is not None:
                if recovered_exam.deleted_at is not None:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "exam_deleted",
                            "message": "Đề đã bị xóa trên web; bản local không được tải đè.",
                            "current_revision": recovered_exam.content_revision,
                        },
                    )
                row.exam_id = recovered_exam.id
                row.status = "ready"
                row.updated_at = utcnow()
                return {
                    "status": "ready",
                    "exam_id": recovered_exam.id,
                    "revision": recovered_exam.content_revision,
                }
        expected_ids = {
            item["asset_id"] for item in (row.manifest.get("assets") or [])
        }
        if expected_ids - set((row.uploaded_assets or {}).keys()):
            raise HTTPException(status_code=409, detail="Chưa tải đủ asset")
        if row.exam_id and row.status == "ready":
            exam = session.get(Exam, row.exam_id)
            return {
                "status": "ready",
                "exam_id": row.exam_id,
                "revision": exam.content_revision if exam else None,
            }
        if row.status == "completing":
            if _completion_is_recent(row):
                raise HTTPException(
                    status_code=409,
                    detail="Đề đang được hoàn tất; hãy thử lại sau.",
                )
        manifest = deepcopy(row.manifest or {})
        uploaded = deepcopy(row.uploaded_assets or {})
        target_exam_id = row.exam_id
        client_exam_id = row.client_exam_id
        row.status = "completing"
        row.updated_at = utcnow()

    payload = deepcopy(manifest["payload"])
    audios = list(payload.get("audios") or [])
    if payload.get("audio") and not any(a.get("id") == payload["audio"].get("id") for a in audios):
        audios.append(payload["audio"])
    payload["audios"] = audios

    for stimulus in payload.get("stimuli") or []:
        for asset in stimulus.get("assets") or []:
            asset["url"] = (
                f"/api/v1/exams/__EXAM_ID__/assets/{asset.get('id')}"
            )
    for audio in payload.get("audios") or []:
        audio["url"] = f"/api/v1/exams/__EXAM_ID__/assets/{audio.get('id')}"
    if payload.get("audio"):
        payload["audio"]["url"] = f"/api/v1/exams/__EXAM_ID__/assets/{payload['audio'].get('id')}"

    from platform_api import persist_final_exam

    try:
        exam_id = persist_final_exam(
            payload,
            job_id=None,
            owner_user_id=identity["user_id"],
            title=manifest["title"],
            category=manifest.get("category", ""),
            target_exam_id=target_exam_id,
            client_exam_id=client_exam_id,
            base_revision=manifest.get("base_revision"),
            defer_version_snapshot=True,
        )
    except Exception:
        with session_scope() as session:
            failed = session.get(DesktopSync, sync_id, with_for_update=True)
            if failed is not None and failed.status == "completing":
                failed.status = "uploading"
                failed.updated_at = utcnow()
        raise
    if not exam_id:
        raise HTTPException(status_code=500, detail="Không thể lưu đề")
    payload_text = str(exam_id)
    for stimulus in payload.get("stimuli") or []:
        for asset in stimulus.get("assets") or []:
            asset["url"] = asset["url"].replace("__EXAM_ID__", payload_text)
    for audio in payload.get("audios") or []:
        audio["url"] = audio["url"].replace("__EXAM_ID__", payload_text)
    if payload.get("audio"):
        payload["audio"]["url"] = payload["audio"]["url"].replace("__EXAM_ID__", payload_text)

    with session_scope() as session:
        exam = session.get(Exam, exam_id)
        row = session.get(DesktopSync, sync_id, with_for_update=True)
        if exam is None or row is None:
            raise HTTPException(status_code=409, detail="Phiên đồng bộ đã thay đổi")
        exam.client_exam_id = client_exam_id
        exam.category = manifest.get("category", "")
        exam.payload = payload
        payload_assets = [
            asset
            for stimulus in payload.get("stimuli") or []
            for asset in stimulus.get("assets") or []
        ] + list(payload.get("audios") or [])
        if payload.get("audio") and payload["audio"] not in payload_assets:
            payload_assets.append(payload["audio"])

        asset_id_by_filename = {}
        for item in payload_assets:
            if item.get("id"):
                asset_id_by_filename[str(item.get("id"))] = str(item.get("id"))
                if item.get("filename"):
                    asset_id_by_filename[str(item.get("filename"))] = str(item.get("id"))

        asset_rows = list(
            session.scalars(select(Asset).where(Asset.exam_id == exam_id))
        )
        for asset in asset_rows:
            payload_asset_id = asset_id_by_filename.get(asset.filename, asset.filename)
            uploaded_asset = uploaded.get(payload_asset_id) or uploaded.get(asset.filename)
            if uploaded_asset:
                asset.bucket = uploaded_asset["bucket"]
                asset.object_key = uploaded_asset["object_key"]
                asset.sha256 = uploaded_asset["sha256"]
                asset.size = uploaded_asset["size"]

        for item in manifest.get("assets") or []:
            if item.get("kind") != "source":
                continue
            source_upload = uploaded.get(str(item.get("asset_id")))
            if not source_upload:
                continue
            match = re.fullmatch(
                r"source-(listening|reading|combined|main)\.pdf",
                str(item.get("asset_id") or ""),
            )
            component = match.group(1) if match else "main"
            source = session.scalar(
                select(ExamSource).where(
                    ExamSource.exam_id == exam.id,
                    ExamSource.component == component,
                )
            )
            if source is None:
                source = ExamSource(
                    exam_id=exam.id,
                    component=component,
                    bucket=source_upload["bucket"],
                    object_key=source_upload["object_key"],
                    filename=str(item.get("filename") or item.get("asset_id")),
                )
                session.add(source)
            source.bucket = source_upload["bucket"]
            source.object_key = source_upload["object_key"]
            source.size = int(source_upload["size"])
            source.sha256 = source_upload["sha256"]
        from classroom_api import _snapshot_exam

        version = _snapshot_exam(session, exam, identity["user_id"])
        exam.current_version_id = version.id
        row.exam_id = exam_id
        row.status = "ready"
        row.updated_at = utcnow()
        revision = exam.content_revision
    return {"status": "ready", "exam_id": exam_id, "revision": revision}


@router.get("/sync/reconcile")
def reconcile_syncs(request: Request) -> dict[str, Any]:
    """Return bounded server truth for Desktop-originated exam mappings."""
    identity = current_identity(request)
    with session_scope() as session:
        rows = session.scalars(
            select(DesktopSync)
            .where(DesktopSync.user_id == identity["user_id"])
            .order_by(DesktopSync.updated_at.desc())
            .limit(1000)
        ).all()
        items: list[dict[str, Any]] = []
        for row in rows:
            exam = session.get(Exam, row.exam_id) if row.exam_id else None
            if exam is None:
                continue
            items.append(
                {
                    "client_exam_id": row.client_exam_id,
                    "exam_id": exam.id,
                    "revision": max(1, int(exam.content_revision or 1)),
                    "deleted": exam.deleted_at is not None,
                }
            )
    return {"items": items}


@router.get("/sync/exams/{sync_id}")
def sync_status(sync_id: str, request: Request) -> dict[str, Any]:
    identity = current_identity(request)
    sync = _owned_sync(sync_id, identity["user_id"])
    return {
        "sync_id": sync.id,
        "status": sync.status,
        "exam_id": sync.exam_id,
        "uploaded_assets": sorted((sync.uploaded_assets or {}).keys()),
    }


@router.get("/exams/{exam_id}/assets/{asset_id}")
def protected_asset(exam_id: str, asset_id: str, request: Request):
    identity = current_identity(request)
    with session_scope() as session:
        exam = session.get(Exam, exam_id)
        if not exam or exam.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Không tìm thấy exam")
        if (
            exam.owner_user_id != identity.get("user_id")
            and str(identity.get("role") or "").casefold() != "admin"
        ):
            raise HTTPException(status_code=403, detail="Không có quyền truy cập asset này")
        asset = session.scalar(
            select(Asset).where(Asset.exam_id == exam_id, Asset.filename == asset_id)
        )
        if asset is None:
            payload_assets = [
                asset_ref
                for stimulus in (exam.payload or {}).get("stimuli") or []
                for asset_ref in stimulus.get("assets") or []
            ] + list((exam.payload or {}).get("audios") or [])
            matching = next(
                (
                    item
                    for item in payload_assets
                    if str(item.get("id")) == asset_id
                ),
                None,
            )
            if matching:
                asset = session.scalar(
                    select(Asset).where(
                        Asset.exam_id == exam_id,
                        Asset.filename == str(matching.get("filename") or asset_id),
                    )
                )
        if not asset:
            raise HTTPException(status_code=404, detail="Không tìm thấy asset")
        bucket, object_key, content_type = (
            asset.bucket,
            asset.object_key,
            asset.content_type,
        )
    if storage is None:
        raise HTTPException(status_code=503, detail="Object storage chưa sẵn sàng")
    response = storage.client.get_object(bucket, storage.safe_key(object_key))

    def body():
        try:
            yield from response.stream(1024 * 1024)
        finally:
            response.close()
            response.release_conn()

    return StreamingResponse(body(), media_type=content_type)
