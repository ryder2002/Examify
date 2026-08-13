"""Local OCR helpers for TOEIC answer-key images."""

from __future__ import annotations

import re
import time
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from rapid_ocr import OCRResult, recognize, recognize_text, warmup_ocr


ANSWER_TOKEN = re.compile(
    r"(?<![0-9A-Za-z])(?P<number>[0-9IlOi|ODQ]{1,3})\s*[\.\:\-\(\[\{]*\s*(?P<letter>[A-Da-d])\s*[\)\}\]]?(?![A-Za-z0-9])",
)
COMPACT_ANSWER_TOKEN = re.compile(
    r"(?<![0-9A-Za-z])(?P<number>[0-9IlOi|ODQ]{1,3})\s*(?P<letter>[A-Da-d])"
    r"(?![A-Za-z])"
)
NUMBER_TRANSLATION = str.maketrans({
    "I": "1", "l": "1", "i": "1", "|": "1",
    "O": "0", "o": "0", "D": "0", "Q": "0",
})
# A five-column key contains 100 tightly packed pairs.  One full-page pass
# plus the bounded grid recovery needs more than the old 15-second budget on
# the remote CPU worker, especially while two API processes are cold-starting
# their bounded OCR sessions.
ANSWER_KEY_OCR_BUDGET_SECONDS = 30.0
ANSWER_KEY_OCR_CALL_TIMEOUT_SECONDS = 6.0
ANSWER_KEY_MIN_CONFIDENCE = 45.0
ANSWER_KEY_TEXT_SCORE = 0.30


class AnswerKeyOcrTimeout(TimeoutError):
    """A bounded OCR pass exhausted the answer-key request budget."""


@lru_cache(maxsize=1)
def _warmup_local_ocr() -> None:
    """Keep engine initialization outside the per-image OCR call budget."""
    warmup_ocr()


def _ocr_text(
    image: Image.Image,
    *,
    config: str,
    text_score: float = ANSWER_KEY_TEXT_SCORE,
    deadline: float | None = None,
) -> str:
    remaining = (
        ANSWER_KEY_OCR_CALL_TIMEOUT_SECONDS
        if deadline is None
        else min(ANSWER_KEY_OCR_CALL_TIMEOUT_SECONDS, deadline - time.monotonic())
    )
    if remaining <= 0:
        raise AnswerKeyOcrTimeout("Đã hết thời gian OCR ảnh đáp án")
    _warmup_local_ocr()
    started = time.monotonic()
    try:
        text = recognize_text(image, text_score=text_score, config=config)
    except Exception as exc:
        if time.monotonic() - started >= remaining:
            raise AnswerKeyOcrTimeout("Tesseract xử lý quá thời gian") from exc
        raise
    if time.monotonic() - started > remaining:
        raise AnswerKeyOcrTimeout("Tesseract xử lý quá thời gian")
    return text


def _ocr_layout(
    image: Image.Image,
    *,
    text_score: float = ANSWER_KEY_TEXT_SCORE,
    deadline: float | None = None,
) -> OCRResult:
    """Run one bounded full-image pass while retaining OCR coordinates."""
    remaining = (
        ANSWER_KEY_OCR_CALL_TIMEOUT_SECONDS
        if deadline is None
        else min(ANSWER_KEY_OCR_CALL_TIMEOUT_SECONDS, deadline - time.monotonic())
    )
    if remaining <= 0:
        raise AnswerKeyOcrTimeout("Đã hết thời gian OCR ảnh đáp án")
    _warmup_local_ocr()
    started = time.monotonic()
    try:
        result = recognize(image, text_score=text_score)
    except Exception as exc:
        if time.monotonic() - started >= remaining:
            raise AnswerKeyOcrTimeout("Tesseract xử lý quá thời gian") from exc
        raise
    if time.monotonic() - started > remaining:
        raise AnswerKeyOcrTimeout("Tesseract xử lý quá thời gian")
    return result


def _layout_answer_candidate(
    image: Image.Image,
    *,
    expected_numbers: set[int] | None,
    deadline: float | None = None,
) -> tuple[dict[int, str], str, list[str]]:
    """Parse unambiguous number/letter pairs from one spatial OCR pass."""
    result = _ocr_layout(image, deadline=deadline)
    answers: dict[int, str] = {}
    duplicates: list[str] = []
    raw_lines: list[str] = []
    for line in result.lines:
        if line.confidence < ANSWER_KEY_MIN_CONFIDENCE:
            continue
        text = line.text
        if not parse_answer_key_text(text)[0] and line.words:
            text = " ".join(word.text for word in sorted(line.words, key=lambda item: min(point[0] for point in item.box)))
        if not text.strip():
            continue
        raw_lines.append(text.strip())
        parsed, conflicts = parse_answer_key_text(text)
        duplicates.extend(conflicts)
        for number, letter in parsed.items():
            if expected_numbers is not None and number not in expected_numbers:
                continue
            previous = answers.get(number)
            if previous is not None and previous != letter:
                duplicates.append(f"{number}{letter}")
                continue
            answers[number] = letter
    return answers, "\n".join(raw_lines), list(dict.fromkeys(duplicates))


def parse_answer_key_text(text: str) -> tuple[dict[int, str], list[str]]:
    answers: dict[int, str] = {}
    duplicates: list[str] = []
    for match in ANSWER_TOKEN.finditer(text):
        raw_number = match.group("number").translate(NUMBER_TRANSLATION)
        if not raw_number.isdigit():
            continue
        number = int(raw_number)
        if not 1 <= number <= 200:
            continue
        letter = match.group("letter").upper()
        if number in answers and answers[number] != letter:
            duplicates.append(f"{number}{letter}")
            continue
        answers[number] = letter

    # Some mobile OCR results remove the parentheses and spaces entirely
    # (``1B2C3D``).  Recover those compact pairs without accepting letters
    # embedded in normal words.  The normal pattern above remains primary so
    # this fallback cannot overwrite a clearly parsed answer.
    for match in COMPACT_ANSWER_TOKEN.finditer(text):
        raw_number = match.group("number").translate(NUMBER_TRANSLATION)
        if not raw_number.isdigit():
            continue
        number = int(raw_number)
        if not 1 <= number <= 200:
            continue
        letter = match.group("letter").upper()
        if number in answers:
            if answers[number] != letter:
                duplicates.append(f"{number}{letter}")
            continue
        answers[number] = letter

    return answers, duplicates


def answer_key_scope_detail(
    raw_text: str, expected_numbers: set[int]
) -> str | None:
    """Explain a complete-range mismatch without weakening job validation."""
    if not expected_numbers:
        return None
    observed, _ = parse_answer_key_text(raw_text)
    out_of_scope = sorted(set(observed) - expected_numbers)
    if not out_of_scope or set(observed).intersection(expected_numbers):
        return None
    observed_min = min(out_of_scope)
    observed_max = max(out_of_scope)
    current_min = min(expected_numbers)
    detected_type = "Reading (101–200)" if observed_min >= 101 else "Listening (1–100)"
    current_type = "Reading (101–200)" if current_min >= 101 else "Listening (1–100)"
    return (
        f"Ảnh chứa đáp án {detected_type}, nhưng đề hiện tại là {current_type}. "
        f"Hãy mở đúng đề tương ứng rồi dán lại ảnh (đã nhận thấy câu {observed_min}–{observed_max})."
    )


def _preprocess_variants(image: Image.Image) -> list[Image.Image]:
    """Return complementary high-contrast renderings for compact answer grids."""
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(1.65)
    softened = gray.filter(ImageFilter.MedianFilter(size=3))
    threshold = softened.point(lambda pixel: 255 if pixel > 168 else 0, mode="1").convert("L")
    return [gray, softened, threshold]


def _fast_full_page_candidate(
    image: Image.Image,
    deadline: float | None = None,
    *,
    text_score: float = ANSWER_KEY_TEXT_SCORE,
) -> tuple[dict[int, str], str, list[str]]:
    """Read a clean answer table once before starting recovery passes."""
    fast = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
    try:
        text = _ocr_text(
            fast,
            config="answer-key-full-page",
            text_score=text_score,
            deadline=deadline,
        )
    finally:
        fast.close()
    answers, duplicates = parse_answer_key_text(text)
    return answers, text, duplicates


def _column_crops(image: Image.Image, count: int) -> list[Image.Image]:
    """OCR each answer-key column separately, with a small overlap at edges."""
    overlap = max(8, round(image.width * 0.012))
    crops: list[Image.Image] = []
    for index in range(count):
        left = max(0, round(image.width * index / count) - overlap)
        right = min(image.width, round(image.width * (index + 1) / count) + overlap)
        crops.append(image.crop((left, 0, right, image.height)))
    return crops


def _answer_table_body(image: Image.Image) -> Image.Image:
    """Trim only the outer border from a compact 100-answer photo.

    A fixed 11.5% top crop used to discard the first two or three answers when
    a teacher selected a tight screenshot with no title band. Heading text is
    now ignored by pair-aware parsing, so preserving the top rows is safer
    across both screenshots and printed answer sheets.
    """
    left = round(image.width * 0.04)
    right = round(image.width * 0.96)
    top = round(image.height * 0.01)
    bottom = round(image.height * 0.98)
    return image.crop((left, top, max(left + 1, right), max(top + 1, bottom)))


def _is_compact_five_column_photo(
    image: Image.Image, expected_numbers: set[int] | None
) -> bool:
    if expected_numbers is None or len(expected_numbers) != 100:
        return False
    ratio = image.height / max(1, image.width)
    # The 5 x 20 answer-key photos are tall but substantially shorter than a
    # 4-column scan.  This avoids routing arbitrary answer text through the
    # geometry shortcut.
    return 1.20 <= ratio <= 1.70


def _preferred_grid_column_counts(full_page_text: str) -> tuple[int, int]:
    """Choose the likely table width before launching per-column OCR.

    Most answer sheets use five answers per visual row, including both
    1-100 and 101-200 keys. Some older exports use four columns. A partial
    full-page OCR result is enough to distinguish them without another
    OCR pass; ambiguous images default to the common five-column
    layout and retain four columns as the bounded fallback.
    """
    row_widths = [
        len(parse_answer_key_text(line)[0])
        for line in full_page_text.splitlines()
    ]
    four_column_rows = row_widths.count(4)
    five_column_rows = row_widths.count(5)
    return (4, 5) if four_column_rows > five_column_rows else (5, 4)


def _grid_answer_candidate(
    image: Image.Image,
    expected_numbers: set[int],
    column_count: int,
    deadline: float | None = None,
    *,
    text_score: float = ANSWER_KEY_TEXT_SCORE,
) -> tuple[dict[int, str], str] | None:
    """Read a regular multi-column key without trusting OCR's digit output.

    TOEIC keys commonly use either 5 columns × 20 rows in visual row order or
    4 columns × 25 rows in column order. OCR often recognizes the answer
    letter perfectly while confusing a small digit. Once every row is found,
    its position is more reliable than that digit, so bind its final A-D letter
    to the known expected-number sequence.
    """
    numbers = sorted(expected_numbers)
    if not numbers or len(numbers) % column_count:
        return None
    rows_per_column = len(numbers) // column_count
    answers: dict[int, str] = {}
    texts: list[str] = []
    explicit_evidence = 0
    for index, crop in enumerate(_column_crops(image, column_count)):
        try:
            text = _ocr_text(
                crop,
                config="answer-key-grid",
                text_score=text_score,
                deadline=deadline,
            )
        except AnswerKeyOcrTimeout:
            raise
        except Exception:
            return None
        finally:
            crop.close()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        row_letters: list[str] = []
        for line in lines:
            # Prefer letters attached to a number. A broad `[A-D]` search
            # reads the A in headings or ordinary words and shifts every
            # following row. If a digit was blurred, retain only an explicit
            # parenthesized/terminal option marker as the positional fallback.
            parsed_line, _line_conflicts = parse_answer_key_text(line)
            if parsed_line:
                row_letters.extend(parsed_line.values())
                continue
            markers = re.findall(
                r"(?<![A-Za-z])[\(\[\{][ \t]*([A-Da-d])[ \t]*[\)\]\}]"
                r"|(?<![A-Za-z])([A-Da-d])(?=[ \t]*$)",
                line,
            )
            row_letters.extend(
                (first or second).upper() for first, second in markers
            )
        row_letters = row_letters[:rows_per_column]
        explicit, _duplicates = parse_answer_key_text(text)
        start = index * rows_per_column
        column_major_numbers = numbers[start : start + rows_per_column]
        row_major_numbers = numbers[index::column_count]
        explicit_numbers = set(explicit)
        # Answer keys occur in both forms:
        # - column major: 1..25, 26..50, ...
        # - row major:    101..105 on the first row, 106..110 on the next.
        # A few explicitly recognized numbers reliably identify the layout.
        column_major_score = len(explicit_numbers.intersection(column_major_numbers))
        row_major_score = len(explicit_numbers.intersection(row_major_numbers))
        mapped_numbers = (
            row_major_numbers
            if row_major_score > column_major_score
            else column_major_numbers
        )
        expected_column = set(mapped_numbers)
        explicit_evidence += len(explicit_numbers.intersection(expected_column))
        # Do not remap a confidently numbered key from another section by
        # visual position.  For example, a Reading 101–200 image pasted into
        # a Listening 1–100 review must be rejected, not converted into a
        # plausible-looking but completely wrong 1–100 key.
        if explicit_numbers and not explicit_numbers.intersection(expected_column):
            return None
        answers.update(
            {
                number: letter
                for number, letter in explicit.items()
                if number in expected_column
            }
        )
        # Mapping by row is only safe if every expected row was recognized.
        # Otherwise retain the explicitly numbered rows as a partial candidate
        # so later passes only need to recover the genuinely missing entries.
        if len(row_letters) == rows_per_column:
            for offset, letter in enumerate(row_letters):
                answers[mapped_numbers[offset]] = letter
        texts.append(text)
    # If every number disappeared, positional remapping could turn a 101-200
    # image into a plausible-looking 1-100 key. Require at least one number
    # from the current range before trusting row positions.
    if not explicit_evidence:
        return None
    return (answers, "\n".join(texts)) if answers else None


def extract_answer_key_image(
    payload: bytes, expected_numbers: set[int] | None = None
) -> tuple[dict[int, str], str, list[str]]:
    try:
        with Image.open(BytesIO(payload)) as source:
            source.verify()
        with Image.open(BytesIO(payload)) as source:
            if source.mode in ("RGBA", "LA") or (source.mode == "P" and "transparency" in source.info):
                bg = Image.new("RGB", source.size, (255, 255, 255))
                converted = source.convert("RGBA")
                bg.paste(converted, mask=converted.split()[3])
                image = bg
            else:
                image = source.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Ảnh đáp án không hợp lệ") from exc

    # Bound both dimensions. A narrow phone screenshot can otherwise become a
        # very large bitmap and make every OCR recovery pass disproportionately
    # slow. Answer-key letters are large enough at 1000px and do not benefit
    # from the 1500px page-OCR target.
    target_width = 1000 if expected_numbers is not None else 1500
    target_height = 1800 if expected_numbers is not None else 2400
    scale = min(
        3.0,
        target_width / max(1, image.width),
        target_height / max(1, image.height),
    )
    if (
        image.width > target_width
        or image.height > target_height
        or image.width < target_width
    ):
        resized = image.resize(
            (
                max(1, round(image.width * scale)),
                max(1, round(image.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
        image.close()
        image = resized

    candidates: list[tuple[dict[int, str], str, list[str]]] = []
    target_count = len(expected_numbers) if expected_numbers else 100
    deadline = time.monotonic() + ANSWER_KEY_OCR_BUDGET_SECONDS
    timed_out = False
    fast_text = ""

    def observed_numbers() -> set[int]:
        observed = {
            number
            for answers, _text, _duplicates in candidates
            for number in answers
        }
        return observed.intersection(expected_numbers) if expected_numbers else observed

    compact_photo = _is_compact_five_column_photo(image, expected_numbers)
    if compact_photo:
        # The image in the review screen is a regular 5 x 20 table with a
        # coloured title band. OCR the clean body first; this avoids paying
        # for a full-page layout pass that is known to be detector-hostile.
        table = _answer_table_body(image)
        table_gray = ImageOps.autocontrast(ImageOps.grayscale(table), cutoff=1)
        try:
            body_candidate = _fast_full_page_candidate(
                table_gray,
                deadline,
                text_score=ANSWER_KEY_TEXT_SCORE,
            )
            body_answers = {
                number: letter
                for number, letter in body_candidate[0].items()
                if expected_numbers is None or number in expected_numbers
            }
            candidates.append((body_answers, body_candidate[1], body_candidate[2]))
            fast_text = body_candidate[1]
        except AnswerKeyOcrTimeout:
            timed_out = True
        except Exception:
            pass

        # Crop-first recovery is only used when the single body pass is
        # incomplete. Each OCR input is one column, so the detector does not
        # need to resolve the coloured header and all 100 pairs at once.
        if not timed_out and len(observed_numbers()) < target_count:
            grid = None
            try:
                grid = _grid_answer_candidate(
                    table_gray,
                    expected_numbers or set(),
                    5,
                    deadline,
                    text_score=ANSWER_KEY_TEXT_SCORE,
                )
            except AnswerKeyOcrTimeout:
                timed_out = True
            except Exception:
                grid = None
            if grid:
                answers, text = grid
                candidates.append((answers, text, []))
        table_gray.close()
        table.close()
    else:
        # First retain the OCR spatial result. The text-only path below
        # remains a bounded compatibility/recovery path for layouts that do
        # not form a complete number/letter pair on one detected line.
        try:
            layout_candidate = _layout_answer_candidate(
                image,
                expected_numbers=expected_numbers,
                deadline=deadline,
            )
            if layout_candidate[0]:
                candidates.append(layout_candidate)
        except AnswerKeyOcrTimeout:
            timed_out = True
        except Exception:
            pass

        # Fast path: one OCR call is sufficient for clean answer text.
        try:
            fast_candidate = _fast_full_page_candidate(image, deadline)
            candidates.append(fast_candidate)
            fast_text = fast_candidate[1]
            fast_observed = (
                set(fast_candidate[0]).intersection(expected_numbers)
                if expected_numbers
                else set(fast_candidate[0])
            )
            if target_count >= 20 and len(fast_observed) >= target_count:
                image.close()
                return fast_candidate
        except AnswerKeyOcrTimeout:
            timed_out = True
        except Exception:
            pass

    if not compact_photo:
        variants = _preprocess_variants(image)
    else:
        variants = []

    # Bounded recovery: regular TOEIC keys use four or five columns. The old
    # pipeline continued with every column and dozens of individual row crops;
    # one poor image could therefore launch 50+ OCR passes.
    if not compact_photo and not timed_out and expected_numbers and len(expected_numbers) >= 20:
        for column_count in _preferred_grid_column_counts(fast_text):
            try:
                grid = _grid_answer_candidate(
                    variants[0], expected_numbers, column_count, deadline
                )
            except AnswerKeyOcrTimeout:
                timed_out = True
                break
            if grid:
                answers, text = grid
                candidates.append((answers, text, []))
                if len(observed_numbers()) >= target_count:
                    break

    # At most two complementary full-page recovery passes. They improve noisy
    # scans while keeping the total subprocess count and wall time predictable.
    if not compact_photo and not timed_out and (
        len(observed_numbers()) < target_count or target_count < 20
    ):
        for variant, psm in zip(variants[:2], (4, 11)):
            try:
                text = _ocr_text(
                    variant,
                    config=(
                        f"answer-key-recovery-{psm}"
                    ),
                    text_score=ANSWER_KEY_TEXT_SCORE,
                    deadline=deadline,
                )
            except AnswerKeyOcrTimeout:
                timed_out = True
                break
            except Exception:
                continue
            answers, duplicates = parse_answer_key_text(text)
            candidates.append((answers, text, duplicates))

    # Keep the consensus across several OCR layouts rather than trusting the
    # single pass that happens to return the largest (often noisy) token count.
    votes: dict[int, dict[str, int]] = {}
    for answers, _, _ in candidates:
        for number, letter in answers.items():
            if expected_numbers is not None and number not in expected_numbers:
                continue
            votes.setdefault(number, {}).setdefault(letter, 0)
            votes[number][letter] += 1
    merged: dict[int, str] = {}
    consensus_issues: list[str] = []
    for number, letters in votes.items():
        ranked = sorted(letters.items(), key=lambda item: (-item[1], item[0]))
        winner, winner_votes = ranked[0]
        if len(ranked) > 1 and ranked[1][1] == winner_votes:
            consensus_issues.append(f"{number}: xung đột OCR ({'/'.join(letter for letter, _ in ranked)})")
            continue
        merged[number] = winner

    def candidate_score(candidate: tuple[dict[int, str], str, list[str]]) -> int:
        answers = candidate[0]
        if expected_numbers is not None:
            return len(set(answers).intersection(expected_numbers))
        return len(answers)

    if not candidates:
        for variant in variants:
            variant.close()
        image.close()
        return {}, "", [
            (
                "OCR đã dừng sau 30 giây để tránh treo ứng dụng"
                if timed_out
                else "Không thể chạy OCR ảnh đáp án"
            )
        ]
    best = max(candidates, key=candidate_score)
    # A tied consensus must not silently drop a number that OCR actually read.
    # Use the strongest complete observation as a reviewable fallback and keep
    # the conflict warning so the teacher can verify it.
    if expected_numbers is not None:
        for number in sorted(expected_numbers - set(merged)):
            if number in best[0]:
                merged[number] = best[0][number]
                consensus_issues.append(
                    f"{number}: dùng kết quả OCR tốt nhất, cần kiểm tra"
                )
    all_duplicates = list(
        dict.fromkeys(
            [item for _, _, duplicates in candidates for item in duplicates]
            + consensus_issues
            + ([f"OCR đã dừng ở giới hạn {int(ANSWER_KEY_OCR_BUDGET_SECONDS)} giây; kết quả có thể chưa đủ"] if timed_out else [])
        )
    )
    for variant in variants:
        variant.close()
    image.close()
    fallback_answers = best[0]
    if expected_numbers is not None:
        fallback_answers = {
            number: letter
            for number, letter in fallback_answers.items()
            if number in expected_numbers
        }
    return merged or fallback_answers, best[1], all_duplicates
