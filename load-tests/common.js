import exec from "k6/execution";
import http from "k6/http";
import { check } from "k6";
import crypto from "k6/crypto";

const fixturePath = __ENV.LOAD_TEST_FIXTURES || "./fixtures.local.json";
export const fixtures = JSON.parse(open(fixturePath));
export const baseUrl = (__ENV.BASE_URL || "http://127.0.0.1").replace(/\/$/, "");

export function userFor(listName = "activeUsers") {
  const users = fixtures[listName] || [];
  if (!users.length) {
    exec.test.abort(`Fixture list '${listName}' is empty in ${fixturePath}`);
  }
  return users[(exec.vu.idInTest - 1) % users.length];
}

export function headers(user, extra = {}) {
  return {
    Authorization: `Bearer ${user.accessToken}`,
    "Content-Type": "application/json",
    ...(user.deviceKey ? { "X-Examify-Device-Key": user.deviceKey } : {}),
    ...extra,
  };
}

export function api(method, path, body, user, params = {}) {
  const requestParams = {
    tags: { endpoint: path.replace(/[0-9a-f-]{20,}/gi, ":id") },
    headers: headers(user, params.headers || {}),
    timeout: params.timeout || "30s",
  };
  const payload = body === null || body === undefined ? null : JSON.stringify(body);
  return http.request(method, `${baseUrl}${path}`, payload, requestParams);
}

export function expectApi(response, label, accepted = [200]) {
  return check(response, {
    [`${label}: accepted status`]: (item) => accepted.includes(item.status),
    [`${label}: no 5xx`]: (item) => item.status < 500,
  });
}

export function uuid() {
  const bytes = new Uint8Array(crypto.randomBytes(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
