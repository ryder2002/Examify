"""Parser câu hỏi trắc nghiệm từ text thô.

Hỗ trợ nhiều format phổ biến:
- "Câu 1:", "Câu 1.", "Question 1:", "1.", "1)", "1-"
- Đáp án A. B. C. D. hoặc A) B) C) D)
- Đáp án đúng: "Đáp án: B", "Đáp án đúng: B", "Answer: B", "ĐA: B", "Ans: B"
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, asdict
from typing import Iterable

import wordninja


QUESTION_HEADER = re.compile(
    r"""
    (?:^|\n)\s*
    (?:
        Câu\s*          # "Câu 1"
      | Question\s*
      | Cau\s*
      | Q\s*
    )?
    \s*
    (?P<num>\d{1,4})
    \s*                # khoảng trắng
    [\.\)\:\-]         # dấu phân cách
    \s+
    (?P<body>.*?(?=(?:\n\s*(?:Câu|Question|Cau|Q)?\s*\d{1,4}\s*[\.\)\:\-]|\n\s*Questions\s+\d+|\n\s*PART\s+\d+|\n\s*Directions|\n---COLUMN_BREAK---|\n---PAGE_BREAK---|\Z)))
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

OPTION_MARKER = re.compile(
    r"^[ \t]*[0-9*•|>_<~=\-]{0,2}[ \t]*(?:"
    r"\((?P<letter1>[A-Da-d])\)|\[(?P<letter2>[A-Da-d])\]|"
    r"(?P<letter3>[A-Da-d])[\.\)\:\-])[ \t]*",
    re.MULTILINE,
)
OCR_OPTION_MARKER = re.compile(
    r"(?<![A-Za-z0-9])[\(\[\{]?[ \t]*([A-Da-d])[ \t]*"
    r"[\)\]\}\.\:\-](?=[ \t])",
    re.MULTILINE,
)
MALFORMED_OPTION_LINE = re.compile(
    r"(?m)^(?P<prefix>.*?)(?:\n[ \t]*[^\w\s]{1,3}[ \t]+)"
    r"(?P<missing_text>[A-Za-z0-9].*)$",
    re.DOTALL,
)
CIRCLED_OPTION_MARKERS = str.maketrans(
    {
        "Ⓐ": "(A)",
        "Ⓑ": "(B)",
        "Ⓒ": "(C)",
        "Ⓓ": "(D)",
        # OCR may mix ASCII and CJK full-width parentheses in the same
        # page, for example ``(C）``. Normalize only punctuation; detected
        # option text remains untouched.
        "（": "(",
        "）": ")",
        "［": "[",
        "］": "]",
    }
)

ANSWER_PATTERN = re.compile(
    r"(?:đáp\s*án\s*(?:đúng)?|đa|answer|ans)\s*[:\.\-]?\s*(?P<letter>[A-Da-d])\b",
    re.IGNORECASE,
)

# OCR often renders the Part 5 answer blank as dots/dashes and may join
# several English words when a low-quality scan has narrow character spacing.
# Keep this repair scoped to Part 5 question text so names, passages and answer
# choices elsewhere in the test are never changed speculatively.
PART5_BLANK_RUN = re.compile(r"(?:[.,_\-\u2013\u2014\u00b7][ \t]*){2,}")
PART5_SINGLE_BLANK = re.compile(
    r"(?:(?<=[a-z])\s*[.\-\u2013\u2014]\s*(?=[a-z]))|"
    r"(?:(?<=\s)[.\-\u2013\u2014](?=\s))"
)
GLUED_ENGLISH_TOKEN = re.compile(r"[A-Za-z]{5,}")


def _restore_ocr_english_spacing(value: str) -> str:
    """Split high-confidence glued English tokens from degraded OCR scans."""

    # OCR sometimes joins two words but keeps the second capital, which is a
    # stronger boundary than a language-model guess (``theJasper``).
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    common_join_words = {"a", "an", "is", "of", "the", "to"}

    def split_token(match: re.Match[str]) -> str:
        token = match.group(0)
        pieces = wordninja.split(token)
        # Short capitalized tokens are frequently surnames/product names. Long
        # sentence fragments remain eligible when a common word is visibly
        # joined at either edge (``Thefactory`` / ``Donationsto``).
        if (
            token[0].isupper()
            and len(token) < 14
            and not (
                pieces
                and (
                    pieces[0].lower() in common_join_words
                    or pieces[-1].lower() in common_join_words
                )
            )
        ):
            return token
        if len(pieces) < 2 or any(len(piece) == 1 and piece.lower() not in {"a", "i"} for piece in pieces):
            return token
        restored = " ".join(pieces)
        return restored.capitalize() if token[0].isupper() else restored

    return GLUED_ENGLISH_TOKEN.sub(split_token, value)


def normalize_part5_question_text(value: str) -> str:
    """Preserve one visible TOEIC Part 5 blank and repair glued OCR words."""

    text = _restore_ocr_english_spacing(_normalize(value))
    text = re.sub(r"\btobe\b", "to be", text, flags=re.IGNORECASE)
    text = re.sub(r"^[.,;:]+\s*", "", text)
    text = re.sub(r",(?=\S)", ", ", text)
    text = re.sub(r"\b(Mr|Ms|Mrs|Dr)\.(?=[A-Z])", r"\1. ", text)
    text = re.sub(r"(?<![A-Z])(?<=[a-z])\.(?=[A-Z])", ". ", text)
    text = re.sub(r"(?<='s)(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", text)
    text = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", text)
    text = re.sub(r"\b(Mr|Ms|Mrs|Dr)\.\.+", r"\1.", text)
    if "_" in text:
        text = re.sub(r"_{2,}", " _____ ", text)
        return _normalize(text)

    candidates = list(PART5_BLANK_RUN.finditer(text))
    if candidates:
        # Abbreviations such as ``P.M..`` can also look like a punctuation run.
        # The longest run is the most reliable representation of the exam blank.
        blank = max(candidates, key=lambda match: len(re.sub(r"\s", "", match.group(0))))
    else:
        single_candidates = list(PART5_SINGLE_BLANK.finditer(text))
        if not single_candidates:
            # TOEIC Part 5 always has one blank. When a weak scan drops the
            # blank glyph entirely, keeping an explicit trailing placeholder
            # is safer than presenting the sentence as if it were complete.
            stripped = text.rstrip()
            terminal = stripped[-1] if stripped.endswith((".", "?", "!")) else ""
            base = stripped[:-1].rstrip() if terminal else stripped
            return _normalize(f"{base} _____{terminal}")
        blank = single_candidates[0]

    text = f"{text[:blank.start()]} _____ {text[blank.end():]}"
    return _normalize(text)


PASSAGE_HEADER_PATTERN = re.compile(
    r"Questions\s+(?P<start>\d+)\s*[-–—]\s*(?P<end>\d+)\s+refer\s+to\s+the\s+following\s+(?P<type>[^\.\n]+)[\.\:]?\s*(?P<passage_text>.*?)(?=\n\s*(?:Câu|Question|Cau|Q)?\s*\d{1,4}\s*[\.\)\:\-]|\Z)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class Question:
    number: int
    text: str
    options: dict[str, str]
    correct: str | None
    passage: str | None = None
    part: str = "Part 5"

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if "“" in normalized:
        normalized = re.sub(r"’(?=[?!.])", "”", normalized)
    # A short preposition followed by a line-break hyphen is an OCR artifact,
    # not a compound word (for example "colleague of- Ms. Montaine").
    normalized = re.sub(
        r"\b(of|to|in|on|at|by|for|from)-\s+(?=[A-Z])",
        r"\1 ",
        normalized,
        flags=re.IGNORECASE,
    )
    # Column/page fragments from a neighboring OCR region commonly appear as
    # standalone letters at the end, e.g. "repair T" or "forms. E S T".
    normalized = re.sub(
        r"\s*\b(?:TEST[S50-9]*\s*\d*|Listening\s+test.*|GO\s+ON\s+TO\s+THE\s+NEXT\s+PAGE.*|Page\s+\d+.*)\s*$",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    return re.sub(r"(?:\s+[EST]){1,6}\s*$", "", normalized).rstrip()


def _extract_passages(raw_text: str) -> list[tuple[int, int, str]]:
    """Trích xuất các đoạn văn bài đọc kèm theo dải câu hỏi [start, end]."""
    passages: list[tuple[int, int, str]] = []
    for match in PASSAGE_HEADER_PATTERN.finditer(raw_text):
        try:
            start_q = int(match.group("start"))
            end_q = int(match.group("end"))
            p_text = match.group("passage_text").strip()
            p_type = match.group("type").strip()
            full_passage = f"Questions {start_q}-{end_q} refer to the following {p_type}.\n\n{p_text}"
            passages.append((start_q, end_q, full_passage))
        except ValueError:
            continue
    return passages


def _split_questions(raw_text: str) -> list[tuple[int, str]]:
    """Tách text thô thành danh sách (số câu, nội dung câu)."""
    cleaned = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(QUESTION_HEADER.finditer(cleaned))
    results: list[tuple[int, str]] = []

    for idx, match in enumerate(matches):
        num = int(match.group("num"))
        body = match.group("body").strip()
        results.append((num, body))

    return results


def _extract_options(body: str) -> tuple[str, dict[str, str], str | None]:
    """Tách câu hỏi, các đáp án A-D, và đáp án đúng."""
    body = body.translate(CIRCLED_OPTION_MARKERS)
    # OCR can split a small option marker from its text and return
    # ``B`` / ``evaluate`` as one visual row. Once an explicit option marker
    # has appeared, a line-start bare B-D token is unambiguously the next option marker.
    # Bare A is only converted if no option marker has been seen yet.
    seen_option = False
    normalized_lines: list[str] = []
    for line in body.splitlines():
        if OPTION_MARKER.match(line) or OCR_OPTION_MARKER.search(line):
            seen_option = True
        bare = re.match(r"^[ \t]*([A-Da-d])[ \t]+(.+?)\s*$", line)
        if bare:
            letter = bare.group(1).upper()
            if not seen_option or letter != "A":
                line = f"({letter}) {bare.group(2)}"
        normalized_lines.append(line)
    body = "\n".join(normalized_lines)
    # Tesseract may duplicate the first word of an option before the real
    # marker, for example ``A (D) television set``. Once the marker is
    # visible, a single leading A-D token is an OCR artifact, not part of the
    # option text; remove only that tightly-scoped shape.
    body = re.sub(
        r"(?m)^[ \t]*[A-Da-d][ \t]+(?=[\(\[\{][ \t]*[A-Da-d][ \t]*[\)\]\}])",
        "",
        body,
    )
    # The line-start normalizer above can turn the same artifact into
    # ``(A) (D) television set``. Keep the second, spatially real marker.
    body = re.sub(
        r"(?m)^[ \t]*[\(\[\{][ \t]*[A-Da-d][ \t]*[\)\]\}][ \t]+"
        r"(?=[\(\[\{][ \t]*[A-Da-d][ \t]*[\)\]\}])",
        "",
        body,
    )
    # OCR can place the question stem and its first option on one visual line.
    # Insert a logical line break before that marker, but preserve the compact
    # bullet/number prefixes already supported by OPTION_MARKER (``* (C)`` or
    # ``7 (D)``) so their prefix is not detached from the option line.
    def split_joined_option(match: re.Match[str]) -> str:
        line_start = body.rfind("\n", 0, match.start()) + 1
        prefix = body[line_start : match.start()]
        if re.fullmatch(r"[ \t]*(?:[0-9*•|>_<~=-]{1,2}[ \t]+)?", prefix):
            return f"({match.group(1).upper()}) "
        return f"\n({match.group(1).upper()}) "

    body = OCR_OPTION_MARKER.sub(split_joined_option, body)
    matches = list(OPTION_MARKER.finditer(body))

    if not matches:
        return _normalize(body), {}, None

    # Phần text câu hỏi là đoạn trước đáp án đầu tiên
    question_text = body[: matches[0].start()].strip()

    options: dict[str, str] = {}
    for idx, match in enumerate(matches):
        letter = (match.group("letter1") or match.group("letter2") or match.group("letter3")).upper()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        raw_text = body[start:end].strip()
        next_letter = None
        if idx + 1 < len(matches):
            next_match = matches[idx + 1]
            next_letter = (
                next_match.group("letter1")
                or next_match.group("letter2")
                or next_match.group("letter3")
            ).upper()
        if next_letter and ord(next_letter) == ord(letter) + 2:
            malformed = MALFORMED_OPTION_LINE.match(raw_text)
            if malformed:
                raw_text = malformed.group("prefix").strip()
                missing_letter = chr(ord(letter) + 1)
                options.setdefault(
                    missing_letter,
                    _normalize(malformed.group("missing_text")),
                )
        # A missing question number can make the following question and its
        # choices land inside the previous option's OCR chunk. Once a choice
        # contains a question sentence followed by another option marker, cut
        # the foreign block instead of displaying it as part of the answer.
        embedded = re.search(
            r"\n[ \t]*(?:What|Where|When|Why|Who|How|Which|According)\b",
            raw_text,
            re.IGNORECASE,
        )
        if embedded and re.search(r"\?", raw_text[embedded.start() :]):
            raw_text = raw_text[: embedded.start()].strip()
        clean_text = ANSWER_PATTERN.split(raw_text, maxsplit=1)[0].strip()
        options.setdefault(letter, _normalize(clean_text))

    # Tìm đáp án đúng trong toàn bộ body (kể cả phần sau đáp án D)
    correct: str | None = None
    answer_match = ANSWER_PATTERN.search(body)
    if answer_match:
        correct = answer_match.group("letter").upper()

    return _normalize(question_text), options, correct


def parse_questions(text_split: str, text_full: str | None = None) -> list[Question]:
    """Trả về danh sách Question từ text thô của PDF."""
    if text_full is None:
        text_full = text_split
    passages = _extract_passages(text_full)
    questions: list[Question] = []

    for num, body in _split_questions(text_split):
        if not body:
            continue
        text, options, correct = _extract_options(body)

        # Bỏ qua nếu không có đủ đáp án A-D
        if len(options) < 4:
            continue

        # Tìm passage tương ứng
        matched_passage: str | None = None
        part = "Part 5 - Phần 5"
        for start_q, end_q, p_text in passages:
            if start_q <= num <= end_q:
                matched_passage = p_text
                part = "Part 6 - Phần 6" if end_q <= 134 else "Part 7 - Phần 7"
                break

        if not matched_passage and num >= 147:
            part = "Part 7 - Phần 7"
        elif not matched_passage and num >= 131:
            part = "Part 6 - Phần 6"

        questions.append(
            Question(
                number=num,
                text=text,
                options={k: options[k] for k in ("A", "B", "C", "D") if k in options},
                correct=correct,
                passage=matched_passage,
                part=part,
            )
        )

    # Fallback: nếu regex trên không match, thử tách theo dòng "1." "2." ...
    if not questions:
        questions = _fallback_split(text_split)

    return questions


def _fallback_split(raw_text: str) -> list[Question]:
    """Tách thô theo các dòng bắt đầu bằng số + dấu chấm/ngoặc tròn."""
    chunks = re.split(r"\n\s*(\d{1,4})\s*[\.\)]\s+", raw_text)
    questions: list[Question] = []
    # chunks dạng [prefix, num1, body1, num2, body2, ...]
    it = iter(chunks[1:])
    for num_str, body in zip(it, it):
        try:
            num = int(num_str)
        except ValueError:
            continue
        text, options, correct = _extract_options(body)
        if len(options) < 4 or not text:
            continue
        questions.append(
            Question(
                number=num,
                text=text,
                options={k: options[k] for k in ("A", "B", "C", "D") if k in options},
                correct=correct,
            )
        )
    return questions


def sample_questions(
    questions: list[Question], count: int, shuffle: bool = False
) -> list[Question]:
    """Lấy đúng `count` câu đầu (hoặc xáo trộn rồi lấy)."""
    if not questions:
        return []
    pool = list(questions)
    if shuffle:
        random.shuffle(pool)
    return pool[: max(1, min(count, len(pool)))]


def to_dict_list(questions: Iterable[Question]) -> list[dict]:
    return [q.to_dict() for q in questions]
