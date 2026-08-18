import exec from "k6/execution";
import http from "k6/http";
import { check } from "k6";

import { baseUrl, fixtures } from "./common.js";

const users = fixtures.activeUsers || [];
const mediaUrls = fixtures.mediaUrls || [];

export const options = {
  scenarios: {
    listening_range: {
      executor: "per-vu-iterations",
      vus: Number(__ENV.VUS || 200),
      iterations: 1,
      maxDuration: "45s",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.001"],
    http_req_duration: ["p(95)<500", "p(99)<1000"],
    checks: ["rate>0.999"],
  },
};

export default function () {
  if (!users.length || !mediaUrls.length) exec.test.abort("activeUsers/mediaUrls fixtures are required");
  const index = exec.vu.idInTest - 1;
  const user = users[index % users.length];
  const path = mediaUrls[index % mediaUrls.length];
  const response = http.get(path.startsWith("http") ? path : `${baseUrl}${path}`, {
    headers: {
      Authorization: `Bearer ${user.accessToken}`,
      Range: "bytes=0-262143",
      ...(user.deviceKey ? { "X-Examify-Device-Key": user.deviceKey } : {}),
    },
    tags: { endpoint: "audio-range" },
  });
  check(response, {
    "audio returns range/full response": (item) => item.status === 206 || item.status === 200,
    "audio has bytes": (item) => item.body && item.body.length > 0,
  });
}
