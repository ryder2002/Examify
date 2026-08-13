import type { Question } from "@/lib/utils";

export type SolutionReviewStatus = "correct" | "wrong" | "unanswered" | "ungraded";

export function solutionQuestionStatus(
  question: Question,
  answers: Record<string, string>,
): SolutionReviewStatus {
  const selected = answers[String(question.number)] || "";
  if (!selected) return "unanswered";
  if (!question.correct) return "ungraded";
  return selected === question.correct ? "correct" : "wrong";
}

export function solutionGroupStatus(
  questions: Question[],
  answers: Record<string, string>,
): SolutionReviewStatus {
  const statuses = questions.map((question) =>
    solutionQuestionStatus(question, answers),
  );
  if (statuses.length > 0 && statuses.every((status) => status === "correct")) {
    return "correct";
  }
  if (statuses.includes("wrong")) return "wrong";
  if (statuses.includes("unanswered")) return "unanswered";
  return "ungraded";
}
