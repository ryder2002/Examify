import type { ExamType, Issue, Question, Stimulus } from "@/lib/utils";
import { createClientId } from "@/lib/utils";
import type { OcrLine, PageLayoutEvidence } from "./types";

type ParseResult = { questions: Question[]; stimuli: Stimulus[]; issues: Issue[] };

const PART_RANGES: Record<ExamType, Array<[number, number, string]>> = {
  listening: [
    [1, 6, "part1"],
    [7, 31, "part2"],
    [32, 70, "part3"],
    [71, 100, "part4"],
  ],
  reading: [
    [101, 130, "part5"],
    [131, 146, "part6"],
    [147, 200, "part7"],
  ],
};

function partFor(number: number, examType: ExamType): string {
  return PART_RANGES[examType].find(([start, end]) => number >= start && number <= end)?.[2] ||
    (examType === "listening" ? "part4" : "part7");
}

function optionLettersFor(number: number, examType: ExamType): string[] {
  return examType === "listening" && number >= 7 && number <= 31
    ? ["A", "B", "C"]
    : ["A", "B", "C", "D"];
}

function requiresPrintedText(number: number, examType: ExamType): boolean {
  if (examType === "listening" && number <= 31) return false;
  return !(examType === "reading" && number >= 131 && number <= 146);
}

function requiresPrintedOptions(number: number, examType: ExamType): boolean {
  return !(examType === "listening" && number <= 31);
}

function normalizedLineOrder(left: OcrLine, right: OcrLine): number {
  // TOEIC RC frequently has two columns. A stable column-major order prevents
  // alternating left/right rows when their baselines are close.
  const leftColumn = left.bbox[0] >= 0.48 ? 1 : 0;
  const rightColumn = right.bbox[0] >= 0.48 ? 1 : 0;
  if (left.page !== right.page) return left.page - right.page;
  if (leftColumn !== rightColumn) return leftColumn - rightColumn;
  return left.bbox[1] - right.bbox[1] || left.bbox[0] - right.bbox[0];
}

function cleanText(value: string): string {
  return value.replace(/\s+/g, " ").replace(/\s+([,.;:?!])/g, "$1").trim();
}

function questionAtLineStart(text: string): { number: number; remainder: string } | null {
  // A bare integer is normally a footer page number. A question needs either
  // punctuation ("32.") or actual text after the number.
  // OCR frequently inserts a space before the punctuation ("92 . Where") or
  // substitutes a closing bracket for the printed period. Accept those
  // harmless variants; page numbers remain filtered by the component range.
  const match = text.match(/^\s*(\d{1,3})\s*(?:[.)\]}]\s*(.*)|\s+(.+))$/);
  if (!match) return null;
  return { number: Number(match[1]), remainder: cleanText(match[2] ?? match[3] ?? "") };
}

function splitPrintedOptions(text: string): {
  prefix: string;
  options: Array<{ letter: string; text: string }>;
} {
  const markers = [...text.matchAll(/(?:^|\s)[([]?\s*([A-D])\s*[)\].:]\s*/gi)];
  if (!markers.length) return { prefix: cleanText(text), options: [] };
  const firstIndex = markers[0].index ?? 0;
  const options = markers.map((marker, index) => {
    const markerEnd = (marker.index ?? 0) + marker[0].length;
    const nextStart = index + 1 < markers.length ? (markers[index + 1].index ?? text.length) : text.length;
    return {
      letter: marker[1].toUpperCase(),
      text: cleanText(text.slice(markerEnd, nextStart)),
    };
  });
  return { prefix: cleanText(text.slice(0, firstIndex)), options };
}

function appendParsedLine(
  question: Question,
  text: string,
  previousOption: string | null,
): string | null {
  const parsed = splitPrintedOptions(text);
  if (parsed.prefix) {
    if (/^PART\s+\d|^Directions\b/i.test(parsed.prefix)) {
      // Structural headings are evidence for layout, not question/option text.
    } else if (previousOption) {
      question.options[previousOption] = cleanText(`${question.options[previousOption] || ""} ${parsed.prefix}`);
    } else {
      question.text = cleanText(`${question.text} ${parsed.prefix}`);
    }
  }
  let currentOption = previousOption;
  for (const option of parsed.options) {
    currentOption = option.letter;
    question.options[currentOption] = cleanText(`${question.options[currentOption] || ""} ${option.text}`);
    if (!question.option_letters.includes(currentOption)) question.option_letters.push(currentOption);
  }
  return currentOption;
}

function makeIssue(
  code: string,
  message: string,
  questionNumber: number,
  page: number | null,
  severity: Issue["severity"] = "error",
): Issue {
  return { code, message, question_number: questionNumber, page, severity };
}

function expandEmbeddedQuestionAnchors(line: OcrLine, start: number, end: number): OcrLine[] {
  // Sparse/page-column OCR can concatenate the tail of one question with the
  // next anchor. Split those anchors before stateful parsing so one bad line
  // cannot make the following question disappear. The bbox is retained as
  // evidence (the review crop remains authoritative); token geometry is never
  // used to invent answer text here.
  const matches = [...line.text.matchAll(/(?:^|\s)(\d{1,3})\s*[.)]\s+/g)].filter((match) => {
    const number = Number(match[1]);
    return number >= start && number <= end;
  });
  if (matches.length <= 1) return [line];
  return matches.map((match, index) => {
    const startIndex = match.index || 0;
    const contentStart = startIndex + match[0].lastIndexOf(match[1]);
    const nextStart = index + 1 < matches.length ? (matches[index + 1].index || line.text.length) : line.text.length;
    return { ...line, text: line.text.slice(contentStart, nextStart).trim(), tokens: [] };
  });
}

export function parseToeicPages(
  pages: PageLayoutEvidence[],
  examType: ExamType,
  requestedCount: number | null,
): ParseResult {
  const start = examType === "listening" ? 1 : 101;
  const naturalEnd = examType === "listening" ? 100 : 200;
  const end = requestedCount ? Math.min(naturalEnd, start + requestedCount - 1) : naturalEnd;
  const lines = pages
    .flatMap((page) => page.lines)
    .flatMap((line) => expandEmbeddedQuestionAnchors(line, start, end))
    .sort(normalizedLineOrder);
  const questionsByNumber = new Map<number, Question & { _page: number | null }>();
  let current: (Question & { _page: number | null }) | null = null;
  let currentOption: string | null = null;
  let questionLeft: number | null = null;

  for (const line of lines) {
    const text = cleanText(line.text);
    if (!text) continue;
    const questionMatch = questionAtLineStart(text);
    const questionNumber = questionMatch?.number ?? null;
    if (questionNumber !== null && questionNumber >= start && questionNumber <= end) {
      current = questionsByNumber.get(questionNumber) || {
        number: questionNumber,
        part: partFor(questionNumber, examType),
        text: "",
        options: {},
        option_letters: [],
        correct: null,
        group_id: null,
        stimulus_id: null,
        confidence: line.confidence,
        issues: [],
        _page: line.page,
      };
      const remainder = questionMatch?.remainder || "";
      currentOption = appendParsedLine(current, remainder, null);
      questionLeft = line.bbox[0];
      current.confidence = Math.min(current.confidence, line.confidence);
      questionsByNumber.set(questionNumber, current);
      continue;
    }
    if (!current) continue;
    // A faint watermark can erase the printed "(A)" marker while leaving its
    // answer text intact.  If that line is visibly indented from the question
    // stem and no option has started yet, preserve the text as the first
    // option.  This is geometry-backed recovery, not guessed answer content.
    const firstRequiredOption = optionLettersFor(current.number, examType)[0];
    if (
      !currentOption &&
      questionLeft !== null &&
      line.bbox[0] - questionLeft >= 0.02 &&
      !current.options[firstRequiredOption]?.trim()
    ) {
      current.options[firstRequiredOption] = cleanText(text);
      if (!current.option_letters.includes(firstRequiredOption)) current.option_letters.push(firstRequiredOption);
      currentOption = firstRequiredOption;
      current.confidence = Math.min(current.confidence, line.confidence);
      continue;
    }
    currentOption = appendParsedLine(current, text, currentOption);
    current.confidence = Math.min(current.confidence, line.confidence);
  }

  const issues: Issue[] = [];
  const questions: Question[] = [];
  for (let number = start; number <= end; number += 1) {
    const question = questionsByNumber.get(number);
    if (!question) {
      const needsText = requiresPrintedText(number, examType);
      const needsOptions = requiresPrintedOptions(number, examType);
      const questionIssues = [
        ...(needsText ? ["question_missing"] : []),
        ...(needsOptions ? ["options_missing"] : []),
        ...(needsText || needsOptions ? ["manual_review"] : []),
      ];
      questions.push({
        number,
        part: partFor(number, examType),
        text: "",
        options: {},
        option_letters: optionLettersFor(number, examType),
        correct: null,
        group_id: null,
        stimulus_id: null,
        confidence: 0,
        issues: questionIssues,
      });
      if (needsText) {
        issues.push(makeIssue("question_missing", `Không nhận diện được nội dung câu ${number}.`, number, null));
      }
      if (needsOptions) {
        issues.push(makeIssue("options_missing", `Không nhận diện được phương án câu ${number}.`, number, null));
      }
      continue;
    }
    const { _page, ...cleanQuestion } = question;
    const requiredLetters = optionLettersFor(number, examType);
    if (!cleanQuestion.text && requiresPrintedText(number, examType)) {
      cleanQuestion.issues.push("text_missing", "manual_review");
      issues.push(makeIssue("text_missing", `Câu ${number} thiếu nội dung.`, number, _page));
    }
    if (
      requiresPrintedOptions(number, examType) &&
      requiredLetters.some((letter) => !cleanQuestion.options[letter]?.trim())
    ) {
      cleanQuestion.issues.push("options_missing", "manual_review");
      issues.push(
        makeIssue(
          "options_missing",
          `Câu ${number} chỉ nhận diện ${cleanQuestion.option_letters.length}/${requiredLetters.length} phương án.`,
          number,
          _page,
        ),
      );
    }
    questions.push(cleanQuestion);
  }

  // Stimulus extraction remains conservative: only make a group when the
  // question range itself proves a standard TOEIC shared-passage group. Crop
  // selection stays editable in review instead of inventing an association.
  const stimuli: Stimulus[] = [];
  if (examType === "reading") {
    const grouped = questions.filter((question) => question.number >= 147);
    for (let index = 0; index < grouped.length; ) {
      const remaining = grouped.length - index;
      const size = remaining >= 5 && grouped[index].number >= 176 ? 5 : Math.min(3, remaining);
      const members = grouped.slice(index, index + size);
      const stimulusId = createClientId();
      for (const member of members) member.stimulus_id = stimulusId;
      stimuli.push({
        id: stimulusId,
        kind: "image",
        title: `Passage câu ${members[0].number}-${members[members.length - 1].number}`,
        assets: [],
        question_numbers: members.map((member) => member.number),
        page_numbers: [],
        confidence: Math.min(...members.map((member) => member.confidence)),
        issues: ["crop_review"],
      });
      index += size;
    }
  }
  return { questions, stimuli, issues };
}
