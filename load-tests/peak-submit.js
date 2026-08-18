import exec from "k6/execution";
import { sleep } from "k6";

import { api, expectApi, fixtures } from "./common.js";

const users = fixtures.submitUsers || [];

export const options = {
  scenarios: {
    peak_submit: {
      executor: "per-vu-iterations",
      vus: Number(__ENV.VUS || 200),
      iterations: 1,
      maxDuration: "45s",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.001"],
    http_req_duration: ["p(95)<1000", "p(99)<2000"],
    checks: ["rate>0.999"],
  },
};

export default function () {
  if (!users.length) exec.test.abort("submitUsers fixture is empty");
  const user = users[(exec.vu.idInTest - 1) % users.length];
  sleep(Math.random() * Number(__ENV.SUBMIT_WINDOW_SECONDS || 10));
  const response = api(
    "POST",
    `/api/v1/attempts/${user.attemptId}/submit`,
    {
      answers: user.answers || {},
      time_left_seconds: Number(user.timeLeftSeconds || 0),
      client_revision: Number(user.baseRevision || 0),
    },
    user,
    { headers: { "Idempotency-Key": `load-submit-${user.attemptId}` } },
  );
  expectApi(response, "peak submit");
}
