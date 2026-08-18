import exec from "k6/execution";

import { api, expectApi, fixtures, uuid } from "./common.js";

const users = fixtures.autosaveUsers || fixtures.activeUsers || [];

export const options = {
  scenarios: {
    autosave_spike: {
      executor: "per-vu-iterations",
      vus: Number(__ENV.VUS || 200),
      iterations: 1,
      maxDuration: "45s",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.001"],
    http_req_duration: ["p(95)<300", "p(99)<500"],
    checks: ["rate>0.999"],
  },
};

export default function () {
  if (!users.length) exec.test.abort("autosaveUsers/activeUsers fixture is empty");
  const user = users[(exec.vu.idInTest - 1) % users.length];
  const number = Number(user.firstQuestion || 1);
  const response = api(
    "PATCH",
    `/api/v1/attempts/${user.attemptId}/sync`,
    {
      batch_id: uuid(),
      base_revision: Number(user.baseRevision || 0),
      changes: { [String(number)]: "A" },
      time_left_seconds: 2400,
      presence: { answered_count: 1, current_question_number: number, visibility_state: "visible" },
    },
    user,
  );
  expectApi(response, "simultaneous autosave", [200, 409]);
}
