from exam_slug import build_exam_slug


def test_exam_slug_is_vietnamese_safe_and_unique_by_exam_id() -> None:
    first = build_exam_slug("Đề Thi Thử ETS 2022 – Số 8", "abc0-1234")
    second = build_exam_slug("Đề Thi Thử ETS 2022 – Số 8", "def0-5678")

    assert first == "de-thi-thu-ets-2022-so-8-abc01234"
    assert second == "de-thi-thu-ets-2022-so-8-def05678"
    assert first != second
