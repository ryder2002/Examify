import { sleep } from "k6";

import { api, expectApi, userFor, uuid } from "./common.js";

const vus = Number(__ENV.VUS || 50);
const duration = __ENV.DURATION || "2m";

export const options = {
  scenarios: {
    mixed_exam_workload: {
      executor: "constant-vus",
      vus,
      duration,
      gracefulStop: "15s",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.001"],
    "http_req_duration{expected_response:true}": ["p(95)<300", "p(99)<500"],
    checks: ["rate>0.999"],
  },
};

let revision = 0;

export default function () {
  const user = userFor("activeUsers");
  const roll = Math.random();

  // Most navigation happens entirely in browser state, intentionally producing
  // no request when a learner changes question.
  if (roll < 0.70) {
    sleep(0.35 + Math.random() * 1.2);
    return;
  }
  if (roll < 0.85) {
    const response = api("GET", `/api/v1/attempts/${user.attemptId}/state`, null, user);
    expectApi(response, "reload attempt state");
  } else if (roll < 0.95) {
    const first = Number(user.firstQuestion || 1);
    const last = Number(user.lastQuestion || 100);
    const number = first + Math.floor(Math.random() * (last - first + 1));
    const response = api(
      "PATCH",
      `/api/v1/attempts/${user.attemptId}/sync`,
      {
        batch_id: uuid(),
        base_revision: revision,
        changes: { [String(number)]: ["A", "B", "C", "D"][number % 4] },
        time_left_seconds: 2400,
        presence: {
          answered_count: Math.min(200, revision + 1),
          current_question_number: number,
          is_fullscreen: true,
          visibility_state: "visible",
        },
      },
      user,
    );
    if (response.status === 200) revision = response.json("accepted_revision") || revision + 1;
    expectApi(response, "autosave delta", [200, 409]);
  } else if (user.submitAttemptId) {
    const response = api(
      "POST",
      `/api/v1/attempts/${user.submitAttemptId}/submit`,
      { answers: user.submitAnswers || {}, time_left_seconds: 1200, client_revision: 0 },
      user,
      { headers: { "Idempotency-Key": `mixed-${user.submitAttemptId}` } },
    );
    expectApi(response, "idempotent submit");
  }
  sleep(0.2 + Math.random() * 0.8);
}
