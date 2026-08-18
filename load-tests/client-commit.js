import crypto from "k6/crypto";
import exec from "k6/execution";
import http from "k6/http";
import { check } from "k6";

import { api, baseUrl, expectApi, fixtures, headers, uuid } from "./common.js";

const sourcePath = __ENV.EXTRACTION_SOURCE;
const manifestPath = __ENV.EXTRACTION_MANIFEST;
if (!sourcePath || !manifestPath) {
  throw new Error("EXTRACTION_SOURCE and EXTRACTION_MANIFEST are required");
}
const source = open(sourcePath, "b");
const manifestTemplate = JSON.parse(open(manifestPath));
const teachers = fixtures.clientExtractionUsers || [];

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export const options = {
  scenarios: {
    client_session_commit: {
      executor: "per-vu-iterations",
      vus: Number(__ENV.VUS || 100),
      iterations: 1,
      maxDuration: "5m",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.001"],
    "http_req_duration{endpoint:client-create}": ["p(95)<500"],
    "http_req_duration{endpoint:client-commit}": ["p(95)<3000"],
    checks: ["rate>0.999"],
  },
};

export default function () {
  if (!teachers.length) exec.test.abort("clientExtractionUsers fixture is empty");
  const teacher = teachers[(exec.vu.idInTest - 1) % teachers.length];
  const requestId = uuid();
  const uploadId = `source-${requestId}`;
  const sourceHash = crypto.sha256(source, "hex");
  const createResponse = api(
    "POST",
    "/api/v1/client-extractions",
    {
      client_request_id: requestId,
      component: manifestTemplate.exam_type,
      requested_count: manifestTemplate.requested_count,
      source_sha256: sourceHash,
      pipeline_version: "client-tesseract-v1",
      uploads: [{
        id: uploadId,
        kind: "source",
        filename: manifestTemplate.source_filename,
        content_type: "application/pdf",
        size: source.byteLength,
        sha256: sourceHash,
      }],
    },
    teacher,
    { headers: { "Idempotency-Key": requestId } },
  );
  expectApi(createResponse, "client extraction create", [201]);
  if (createResponse.status !== 201) return;
  const created = createResponse.json();
  const policy = created.uploads[0];
  const uploadFields = { ...policy.fields };
  uploadFields.file = http.file(source, manifestTemplate.source_filename, "application/pdf");
  const uploadResponse = http.post(`${baseUrl}${policy.url}`, uploadFields, {
    tags: { endpoint: "client-upload" },
    timeout: "60s",
  });
  check(uploadResponse, { "direct source upload accepted": (item) => item.status === 204 });
  if (uploadResponse.status !== 204) return;

  const manifest = {
    ...manifestTemplate,
    source_sha256: sourceHash,
    source_size: source.byteLength,
    pipeline_version: "client-tesseract-v1",
    schema_version: 1,
  };
  const manifestHash = crypto.sha256(canonical(manifest), "hex");
  const commitResponse = api(
    "POST",
    `/api/v1/client-extractions/${created.id}/commit`,
    {
      idempotency_key: requestId,
      manifest_sha256: manifestHash,
      manifest,
      title: `Load test extraction ${requestId}`,
      category: "load-test",
      is_full_test_component: false,
    },
    teacher,
    { headers: { "Idempotency-Key": requestId }, timeout: "60s" },
  );
  expectApi(commitResponse, "client extraction commit");
}
