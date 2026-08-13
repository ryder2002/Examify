export type QuizCursor = {
  groupIndex: number;
  questionIndex: number;
};

type NavigableQuizGroup = {
  questions: readonly unknown[];
};

export function moveQuizCursor(
  groups: readonly NavigableQuizGroup[],
  cursor: QuizCursor,
  direction: -1 | 1,
): QuizCursor {
  if (groups.length === 0) return { groupIndex: 0, questionIndex: 0 };

  const groupIndex = Math.min(Math.max(cursor.groupIndex, 0), groups.length - 1);
  const questionCount = Math.max(groups[groupIndex]?.questions.length || 0, 1);
  const questionIndex = Math.min(
    Math.max(cursor.questionIndex, 0),
    questionCount - 1,
  );

  if (direction === 1) {
    if (questionIndex < questionCount - 1) {
      return { groupIndex, questionIndex: questionIndex + 1 };
    }
    if (groupIndex < groups.length - 1) {
      return { groupIndex: groupIndex + 1, questionIndex: 0 };
    }
    return { groupIndex, questionIndex };
  }

  if (questionIndex > 0) {
    return { groupIndex, questionIndex: questionIndex - 1 };
  }
  if (groupIndex > 0) {
    const previousGroupIndex = groupIndex - 1;
    return {
      groupIndex: previousGroupIndex,
      questionIndex: Math.max(
        (groups[previousGroupIndex]?.questions.length || 1) - 1,
        0,
      ),
    };
  }
  return { groupIndex, questionIndex };
}
