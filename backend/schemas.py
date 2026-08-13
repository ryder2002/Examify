"""Shared data helpers for extraction jobs and quiz payloads."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


SCHEMA_VERSION = 2
ExamType = Literal["listening", "reading"]
FinalExamType = Literal["listening", "reading", "combined"]


class Issue(BaseModel):
    code: str
    message: str
    page: int | None = None
    question_number: int | None = None
    severity: Literal["info", "warning", "error"] = "warning"


class AssetRef(BaseModel):
    id: str
    url: str
    page: int
    bbox: list[float] = Field(min_length=4, max_length=4)
    width: int
    height: int


class AudioRef(BaseModel):
    id: str
    url: str
    filename: str
    content_type: str
    size: int
    part: Literal[
        "full",
        "directions_part_1",
        "part_1",
        "part_2",
        "part_3",
        "part_4",
    ] = "full"
    scope: Literal["full", "part", "question", "group"] = "part"
    question_numbers: list[int] = Field(default_factory=list, max_length=100)
    group_id: str | None = Field(default=None, max_length=80)


class Stimulus(BaseModel):
    id: str
    kind: Literal["image"] = "image"
    title: str = ""
    assets: list[AssetRef]
    question_numbers: list[int]
    page_numbers: list[int]
    confidence: float = 100.0
    issues: list[str] = Field(default_factory=list)


class Question(BaseModel):
    number: int
    part: str
    text: str = ""
    options: dict[str, str] = Field(default_factory=dict)
    option_letters: list[str] = Field(default_factory=lambda: ["A", "B", "C", "D"])
    correct: str | None = None
    group_id: str | None = None
    stimulus_id: str | None = None
    confidence: float = 100.0
    issues: list[str] = Field(default_factory=list)


class SolutionEntry(BaseModel):
    key: str = Field(default="", max_length=24)
    question_numbers: list[int] = Field(min_length=1, max_length=3)
    transcript: str | None = Field(default=None, max_length=12_000)
    explanation: str | None = Field(default=None, max_length=12_000)
    translation: str = Field(default="", max_length=12_000)


def expected_question_numbers(
    exam_type: ExamType,
    question_range: tuple[int, int] | None = None,
) -> range:
    """Return the question range represented by an extraction draft.

    A complete TOEIC upload keeps the standard 1–100 / 101–200 range. A
    standalone Part upload may legitimately contain only a contiguous subset,
    such as Listening 7–31 or Reading 101–130; callers can pass that detected
    range so missing-slot coverage does not recreate unrelated questions.
    """
    default_start, default_end = (1, 100) if exam_type == "listening" else (101, 200)
    if question_range is None:
        return range(default_start, default_end + 1)
    start, end = question_range
    if (
        not isinstance(start, int)
        or not isinstance(end, int)
        or start < default_start
        or end > default_end
        or start > end
    ):
        return range(default_start, default_end + 1)
    return range(start, end + 1)


def question_part(exam_type: ExamType, number: int) -> str:
    """Keep manually inserted placeholders in the same part as OCR output."""
    if exam_type == "listening":
        if number <= 6:
            return "Part 1 - Phần 1"
        if number <= 31:
            return "Part 2 - Phần 2"
        if number <= 70:
            return "Part 3 - Phần 3"
        return "Part 4 - Phần 4"
    if number <= 130:
        return "Part 5 - Phần 5"
    if number <= 146:
        return "Part 6 - Phần 6"
    return "Part 7 - Phần 7"


def question_option_letters(exam_type: ExamType, number: int) -> list[str]:
    """Return the answer choices that are printed for a TOEIC question."""
    if exam_type == "listening" and 7 <= number <= 31:
        return ["A", "B", "C"]
    return ["A", "B", "C", "D"]


def question_requires_printed_text(exam_type: ExamType, number: int) -> bool:
    """Whether OCR/manual entry needs a standalone question-text field.

    Listening Part 1/2 is supplied by image/audio, while Reading Part 6 uses
    a passage crop with blank numbers.  Requiring text in those cases would
    leave a legitimate manual repair permanently marked incomplete.
    """
    if exam_type == "listening" and number <= 31:
        return False
    return not (exam_type == "reading" and 131 <= number <= 146)


def question_requires_printed_options(exam_type: ExamType, number: int) -> bool:
    """Listening Part 1/2 are intentionally image/audio-only in a draft."""
    return not (exam_type == "listening" and number <= 31)


def normalize_question_issues(exam_type: ExamType, question: Question) -> Question:
    """Recompute only structural OCR issues after a teacher edits a question."""
    normalized = question.model_copy(deep=True)
    issues = [
        issue
        for issue in normalized.issues
        if issue not in {"question_missing", "options_missing"}
    ]
    number = normalized.number
    if question_requires_printed_text(exam_type, number) and not normalized.text.strip():
        issues.append("question_missing")
    required_letters = normalized.option_letters or question_option_letters(exam_type, number)
    if question_requires_printed_options(exam_type, number) and any(
        not normalized.options.get(letter, "").strip() for letter in required_letters
    ):
        issues.append("options_missing")
    normalized.issues = issues
    return normalized


def manual_question_placeholder(exam_type: ExamType, number: int) -> Question:
    """Create a safe, explicitly incomplete slot for an OCR-missing number."""
    letters = question_option_letters(exam_type, number)
    return Question(
        number=number,
        part=question_part(exam_type, number),
        text="",
        options={letter: "" for letter in letters},
        option_letters=letters,
        correct=None,
        group_id=f"q-{number}",
        stimulus_id=None,
        confidence=0.0,
        issues=["question_missing", "options_missing"],
    )


def ensure_question_coverage(
    exam_type: ExamType,
    questions: list[Question],
    question_range: tuple[int, int] | None = None,
) -> tuple[list[Question], list[int]]:
    """Add placeholders only inside the detected/requested question range.

    Existing questions are never overwritten.  Out-of-range entries are kept
    so a teacher does not lose an unsaved edit; the caller may validate them
    separately if the workflow requires a strict TOEIC-only draft.
    """
    by_number = {question.number: question for question in questions}
    inserted: list[int] = []
    for number in expected_question_numbers(exam_type, question_range):
        if number not in by_number:
            by_number[number] = manual_question_placeholder(exam_type, number)
            inserted.append(number)
    normalized = [
        normalize_question_issues(exam_type, question)
        for question in by_number.values()
    ]
    return sorted(normalized, key=lambda question: question.number), inserted


class ExamDraft(BaseModel):
    schema_version: int = SCHEMA_VERSION
    job_id: str
    exam_type: ExamType
    status: Literal["queued", "processing", "review", "ready", "failed"]
    stage: str
    progress: int = Field(ge=0, le=100)
    processing_phase: Literal[
        "queued", "audio", "audio_ocr", "ocr", "review"
    ] = "queued"
    phase_progress: int = Field(default=0, ge=0, le=100)
    audio_progress: int = Field(default=0, ge=0, le=100)
    ocr_progress: int = Field(default=0, ge=0, le=100)
    audio_stage: str = ""
    ocr_stage: str = ""
    filename: str
    requested_count: int | None = None
    returned_count: int = 0
    questions: list[Question] = Field(default_factory=list)
    stimuli: list[Stimulus] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    error: str | None = None
    cached: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    audio: AudioRef | None = None
    audios: list[AudioRef] = Field(default_factory=list)
    solutions: list[SolutionEntry] = Field(default_factory=list)


class DraftPatch(BaseModel):
    questions: list[Question] | None = None
    stimuli: list[Stimulus] | None = None
    solutions: list[SolutionEntry] | None = None


class ManualStimulusRequest(BaseModel):
    """A crop selected by a teacher from an original rendered PDF page."""

    page: int = Field(ge=1, le=500)
    bbox: list[float] = Field(min_length=4, max_length=4)
    question_numbers: list[int] = Field(min_length=1, max_length=100)
    title: str = Field(default="Ảnh cắt thủ công", max_length=120)


class FinalizeRequest(BaseModel):
    answer_key: dict[str, str] = Field(default_factory=dict)
    count: int | None = Field(default=None, ge=1)
    shuffle: bool = False
    title: str | None = Field(default=None, min_length=1, max_length=255)
    category: str = Field(default="", max_length=120)
    # A desktop edit keeps the existing local record (and its assets) instead
    # of creating a second exam when the teacher returns from review.
    client_exam_id: str | None = Field(default=None, max_length=36)
    # A web Full Test is assembled from two durable 100-question records. Keep
    # those intermediate records out of the shared bank; /exams/combine will
    # publish the single 200-question exam and retire both components.
    is_full_test_component: bool = False


class FinalExam(BaseModel):
    schema_version: int = SCHEMA_VERSION
    job_id: str
    exam_type: FinalExamType
    requested_count: int
    returned_count: int
    total: int
    questions: list[Question]
    stimuli: list[Stimulus]
    audio: AudioRef | None = None
    audios: list[AudioRef] = Field(default_factory=list)
    solutions: list[SolutionEntry] = Field(default_factory=list)
    exam_id: str | None = None
    slug: str | None = None
    title: str | None = None
    category: str = ""
    client_exam_id: str | None = None
    sync_status: str | None = None
    component_job_ids: dict[str, str] = Field(default_factory=dict)
