"""Script repair and sync fix for Exam media assets (Audio + Images)."""

import json
import logging
from database import session_scope
from models import Exam, Asset, ExamVersion, ExamVersionAsset, ClassAssignment, ClassMember
from object_storage import storage
from classroom_api import _exam_for_student, _snapshot_exam
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fix_exam_media_sync")

def fix_all():
    with session_scope() as session:
        exams = session.query(Exam).all()
        logger.info(f"Checking {len(exams)} exams in database...")
        
        for exam in exams:
            payload = dict(exam.payload or {})
            changed = False
            audios = list(payload.get("audios") or [])
            audio_obj = payload.get("audio")
            if audio_obj and not any(a.get("id") == audio_obj.get("id") for a in audios):
                audios.append(audio_obj)
                payload["audios"] = audios
                changed = True
            
            # Check Asset table for this exam
            existing_assets = session.query(Asset).filter_by(exam_id=exam.id).all()
            existing_asset_keys = {a.object_key for a in existing_assets}
            
            # Ensure audio asset is in Asset table
            for audio in audios:
                audio_id = str(audio.get("id", ""))
                audio_filename = str(audio.get("filename") or audio_id)
                # Check if asset exists by kind or filename/id
                has_audio_asset = any(a.kind == "audio" for a in existing_assets)
                if not has_audio_asset:
                    logger.info(f"Creating missing audio Asset record for Exam {exam.id} ({exam.title})")
                    # Try to locate audio file in MinIO
                    possible_keys = [
                        audio.get("url", ""),
                        f"desktop/{exam.owner_user_id}/{exam.client_exam_id}/{audio_id}",
                        f"jobs/{payload.get('job_id')}/audio/{audio_id}",
                    ]
                    found_key = None
                    if storage:
                        for pkey in possible_keys:
                            clean_key = storage.safe_key(pkey.lstrip("/").split("?", 1)[0]) if pkey else ""
                            if clean_key:
                                try:
                                    storage.client.stat_object("examify-audio", clean_key)
                                    found_key = clean_key
                                    break
                                except Exception:
                                    pass
                                try:
                                    storage.client.stat_object("examify-assets", clean_key)
                                    found_key = clean_key
                                    break
                                except Exception:
                                    pass
                    
                    audio_asset = Asset(
                        exam_id=exam.id,
                        kind="audio",
                        bucket="examify-audio",
                        object_key=found_key or possible_keys[1] if exam.client_exam_id else (possible_keys[0] or audio_id),
                        filename=audio_id,
                        content_type=audio.get("content_type") or "audio/mpeg",
                        size=audio.get("size") or 0,
                        display_order=0,
                    )
                    session.add(audio_asset)
                    changed = True
            
            if changed:
                exam.payload = payload
                session.flush()

        # Fix ExamVersions and ExamVersionAssets
        versions = session.query(ExamVersion).all()
        logger.info(f"Checking {len(versions)} exam versions in database...")
        for version in versions:
            exam = session.get(Exam, version.source_exam_id)
            if not exam:
                continue
            
            assets = session.query(ExamVersionAsset).filter_by(exam_version_id=version.id).all()
            has_audio_vasset = any(a.kind == "audio" for a in assets)
            exam_audios = (version.payload or {}).get("audios") or ([version.payload.get("audio")] if (version.payload or {}).get("audio") else [])
            
            if exam_audios and not has_audio_vasset:
                logger.info(f"Re-snapshotting exam version {version.id} for {version.title} to include audio...")
                # Fetch exam assets
                exam_assets = session.query(Asset).filter_by(exam_id=exam.id).all()
                for asset in exam_assets:
                    source_key = asset.object_key.strip().split("?", 1)[0].lstrip("/")
                    if source_key.startswith("api/extractions/"):
                        parts = source_key.split("/")
                        source_key = f"jobs/{parts[2]}/{parts[3]}/{parts[4]}"
                    asset_ref = source_key.rsplit("/", 1)[-1] or asset.filename
                    destination = f"classroom-versions/{version.id}/{asset_ref}"
                    
                    if storage:
                        try:
                            storage.copy_object(asset.bucket, source_key, destination)
                        except Exception as e:
                            logger.warning(f"Copy object failed from {source_key} to {destination}: {e}")
                    
                    # Ensure ExamVersionAsset exists
                    existing = session.query(ExamVersionAsset).filter_by(
                        exam_version_id=version.id,
                        filename=asset_ref,
                    ).first()
                    if not existing:
                        vasset = ExamVersionAsset(
                            exam_version_id=version.id,
                            kind=asset.kind,
                            bucket=asset.bucket,
                            object_key=destination,
                            filename=asset_ref,
                            content_type=asset.content_type,
                            size=asset.size,
                            display_order=asset.display_order,
                        )
                        session.add(vasset)
                session.flush()

        logger.info("Validation phase...")
        for version in versions:
            data = _exam_for_student(session, version, assignment=None, member_id=None)
            logger.info(f"ExamVersion '{version.title}' ({version.id}):")
            if data.get("audio"):
                logger.info(f"  Audio URL: {data['audio'].get('url')}")
            for st in (data.get("stimuli") or [])[:1]:
                for ast in st.get("assets") or []:
                    logger.info(f"  Stimulus Asset URL: {ast.get('url')}")

if __name__ == "__main__":
    fix_all()
