const SPEAKER = "(?:[MWF]\\d?|man|woman|male|female|nam(?:\\s*\\d+)?|nữ(?:\\s*\\d+)?|speaker(?:\\s*\\d+)?|người\\s+nói)";
const NEW_BLOCK = new RegExp(
  `^(?:${SPEAKER}\\s*:|\\([A-D]\\)\\s+|(?:chọn\\s+\\([A-D]\\)\\.?|dẫn\\s+chứng\\b|giải\\s+thích\\b|dịch(?:\\s+bài\\s+đọc)?\\s*:))`,
  "iu",
);
const INLINE_SPEAKER = new RegExp(`([.!?])\\s+(?=${SPEAKER}\\s*:)`, "giu");

/**
 * Undo PDF/OCR soft line wrapping while retaining meaningful dialogue,
 * answer-option and paragraph boundaries.
 */
export function solutionTextParagraphs(value: string | null | undefined): string[] {
  const lines = String(value || "")
    .replace(/\r\n?/g, "\n")
    .replace(INLINE_SPEAKER, "$1\n")
    .split("\n")
    .map((line) => line.trim());
  const paragraphs: string[] = [];
  let current = "";

  const flush = () => {
    const normalized = current.replace(/\s+/g, " ").trim();
    if (normalized) paragraphs.push(normalized);
    current = "";
  };

  for (const line of lines) {
    if (!line) {
      flush();
      continue;
    }
    if (current && NEW_BLOCK.test(line)) flush();
    if (!current) {
      current = line;
    } else if (current.endsWith("-") && /^\p{L}/u.test(line)) {
      current += line;
    } else {
      current += ` ${line}`;
    }
  }
  flush();
  return paragraphs;
}
