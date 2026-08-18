import { apiFetch, isDesktop } from "@/lib/api";
import { createClientId } from "@/lib/utils";
import { getClientOcrBlob, loadClientOcrSource, putClientOcrDraft } from "./local-drafts";
import type { ClientOcrDraft, ClientOcrManifestV1 } from "./types";

type UploadPolicy = {
  upload_id: string;
  kind: "source" | "asset" | "audio";
  url: string;
  method: "POST";
  fields: Record<string, string>;
  expires_in_seconds: number;
};

type SessionResponse = {
  id: string;
  reserved_exam_id: string;
  status: string;
  uploads: UploadPolicy[];
};

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonicalize(item)]),
    );
  }
  return value;
}

async function hashJson(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(JSON.stringify(canonicalize(value)));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function serverManifest(manifest: ClientOcrManifestV1) {
  return {
    schema_version: manifest.schema_version,
    pipeline_version: manifest.pipeline_version,
    source_sha256: manifest.source_sha256,
    source_filename: manifest.source_filename,
    source_size: manifest.source_size,
    page_count: manifest.page_count,
    exam_type: manifest.exam_type,
    requested_count: manifest.requested_count,
    questions: manifest.questions,
    stimuli: manifest.stimuli,
    assets: manifest.assets.map((asset) => ({
      id: asset.id,
      page: asset.page,
      bbox: asset.bbox,
      width: asset.width,
      height: asset.height,
      content_type: asset.contentType,
      size: asset.size,
      upload_id: asset.id,
    })),
    media: (manifest.media || []).map((media) => ({
      id: media.id,
      upload_id: media.uploadId,
      filename: media.filename,
      content_type: media.content_type || "audio/mpeg",
      size: media.size,
      part: media.part,
      scope: media.scope || "part",
      question_numbers: media.question_numbers || [],
      group_id: media.group_id || null,
    })),
    solutions: manifest.solutions || [],
    issues: manifest.issues,
    answer_key: manifest.answer_key,
    metadata: manifest.metadata,
  };
}

async function directUpload(policy: UploadPolicy, blob: Blob): Promise<void> {
  const form = new FormData();
  for (const [key, value] of Object.entries(policy.fields)) form.append(key, value);
  form.append("file", blob);
  const response = await fetch(policy.url, { method: "POST", body: form, credentials: "same-origin" });
  if (!response.ok) throw new Error(`Upload ${policy.upload_id} thất bại (HTTP ${response.status}).`);
}

async function uploadBounded(items: Array<{ policy: UploadPolicy; blob: Blob }>): Promise<void> {
  let cursor = 0;
  const runners = Array.from({ length: Math.min(3, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      await directUpload(items[index].policy, items[index].blob);
    }
  });
  await Promise.all(runners);
}

export async function commitClientOcrDraft(
  draft: ClientOcrDraft,
  title: string,
  category: string,
  options: { targetExamId?: string; baseRevision?: number; isFullTestComponent?: boolean } = {},
): Promise<{ exam_id: string; status: string }> {
  if (!draft.manifest) throw new Error("Draft chưa có manifest OCR.");
  const source = await loadClientOcrSource(draft.key);
  if (!source) throw new Error("Không tìm thấy PDF nguồn local; draft chưa bị xóa nhưng cần chọn lại file.");
  const assetBlobs = await Promise.all(
    draft.manifest.assets.map(async (asset) => {
      const blob = await getClientOcrBlob(asset.localBlobKey);
      if (!blob) throw new Error(`Không tìm thấy crop ${asset.id} trong kho local.`);
      return { asset, blob };
    }),
  );
  const mediaBlobs = await Promise.all(
    (draft.manifest.media || []).map(async (media) => {
      const blob = await getClientOcrBlob(media.localBlobKey);
      if (!blob) throw new Error(`Không tìm thấy media ${media.filename} trong kho local.`);
      return { media, blob };
    }),
  );
  const clientRequestId = draft.clientRequestId || createClientId();
  const manifest = serverManifest(draft.manifest);
  const manifestHash = await hashJson(manifest);
  if (isDesktop()) {
    const form = new FormData();
    const fileIds = [
      `source-${draft.manifest.exam_type}.pdf`,
      ...assetBlobs.map(({ asset }) => asset.id),
      ...mediaBlobs.map(({ media }) => media.id),
    ];
    form.append("manifest", JSON.stringify(manifest));
    form.append("manifest_sha256", manifestHash);
    form.append("client_request_id", clientRequestId);
    form.append("title", title);
    form.append("category", category);
    form.append("file_ids", JSON.stringify(fileIds));
    form.append("files", source, fileIds[0]);
    for (const { asset, blob } of assetBlobs) form.append("files", blob, asset.id);
    for (const { media, blob } of mediaBlobs) form.append("files", blob, media.id);
    const response = await apiFetch("/api/desktop/client-extractions/commit", {
      method: "POST",
      body: form,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(result.detail || "Không lưu được draft OCR vào Desktop.");
    }
    draft.clientRequestId = clientRequestId;
    draft.status = "committed";
    draft.active = false;
    draft.committedAt = new Date().toISOString();
    draft.updatedAt = draft.committedAt;
    await putClientOcrDraft(draft);
    return result as { exam_id: string; status: string };
  }
  let session: SessionResponse;
  if (draft.serverSessionId) {
    const refresh = await apiFetch(`/api/v1/client-extractions/${draft.serverSessionId}/uploads/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        upload_ids: [
          "source",
          ...assetBlobs.map(({ asset }) => asset.id),
          ...mediaBlobs.map(({ media }) => media.uploadId),
        ],
      }),
    });
    const payload = await refresh.json().catch(() => ({}));
    if (!refresh.ok) throw new Error(payload.detail || "Không làm mới được upload policy.");
    session = {
      id: draft.serverSessionId,
      reserved_exam_id: "",
      status: "uploading",
      uploads: (payload.uploads || []).flatMap((item: { uploaded: boolean; policy?: UploadPolicy }) =>
        item.uploaded || !item.policy ? [] : [item.policy],
      ),
    };
  } else {
    const createResponse = await apiFetch("/api/v1/client-extractions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_request_id: clientRequestId,
        component: draft.manifest.exam_type,
        requested_count: draft.manifest.requested_count,
        source_sha256: draft.manifest.source_sha256,
        pipeline_version: draft.manifest.pipeline_version,
        uploads: [
          {
            id: "source",
            kind: "source",
            filename: draft.manifest.source_filename,
            content_type: "application/pdf",
            size: draft.manifest.source_size,
            sha256: draft.manifest.source_sha256,
          },
          ...assetBlobs.map(({ asset, blob }) => ({
            id: asset.id,
            kind: "asset",
            filename: `${asset.id}.webp`,
            content_type: "image/webp",
            size: blob.size,
          })),
          ...mediaBlobs.map(({ media, blob }) => ({
            id: media.uploadId,
            kind: "audio",
            filename: media.filename,
            content_type: media.content_type || blob.type || "audio/mpeg",
            size: blob.size,
          })),
        ],
      }),
    });
    const payload = await createResponse.json().catch(() => ({}));
    if (!createResponse.ok) throw new Error(payload.detail || "Không tạo được upload session.");
    session = payload as SessionResponse;
    draft.serverSessionId = session.id;
    draft.clientRequestId = clientRequestId;
    draft.status = "committing";
    draft.updatedAt = new Date().toISOString();
    await putClientOcrDraft(draft);
  }

  const blobs = new Map<string, Blob>([
    ["source", source] as const,
    ...assetBlobs.map(({ asset, blob }) => [asset.id, blob] as const),
    ...mediaBlobs.map(({ media, blob }) => [media.uploadId, blob] as const),
  ]);
  await uploadBounded(
    session.uploads.map((policy) => {
      const blob = blobs.get(policy.upload_id);
      if (!blob) throw new Error(`Không tìm thấy dữ liệu upload ${policy.upload_id}.`);
      return { policy, blob };
    }),
  );

  const response = await apiFetch(`/api/v1/client-extractions/${session.id}/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      idempotency_key: clientRequestId,
      manifest_sha256: manifestHash,
      manifest,
      title,
      category,
      target_exam_id: options.targetExamId || null,
      base_revision: options.baseRevision || null,
      is_full_test_component: Boolean(options.isFullTestComponent),
    }),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = result.detail;
    throw new Error(typeof detail === "string" ? detail : detail?.message || "Không commit được đề.");
  }
  draft.status = "committed";
  draft.active = false;
  draft.committedAt = new Date().toISOString();
  draft.updatedAt = draft.committedAt;
  await putClientOcrDraft(draft);
  return result as { exam_id: string; status: string };
}
