"""Unit test cho parser - chạy được không cần cài PDF libs."""

import sys
from pathlib import Path

# Cho phép chạy file này mà không cần cài deps backend
sys.path.insert(0, str(Path(__file__).parent))

try:
    from parser import normalize_part5_question_text, parse_questions
except ImportError:
    print("Không import được parser. Hãy cài: pip install -r requirements.txt")
    sys.exit(1)


SAMPLE = """
Câu 1: Thủ đô của Việt Nam là gì?
A. Hà Nội
B. Hồ Chí Minh
C. Đà Nẵng
D. Huế
Đáp án: A

Câu 2: 2 + 2 = ?
A. 3
B. 4
C. 5
D. 6
Đáp án: B

Câu 3: Ngôn ngữ lập trình nào phổ biến nhất 2024?
A. Python
B. JavaScript
C. C++
D. Ruby
Answer: A

Câu 4: HTML là viết tắt của?
A) Hyper Text Markup Language
B) High Tech Machine Language
C) Hyperlinks and Text Markup Language
D) Home Tool Markup Language
Đáp án đúng: A

Question 5: Which is a JS framework?
A. React
B. Django
C. Laravel
D. Flask
Answer: B

109. Dynart, Inc., continuously seeks new ways to reduce use.
(A) seeks
(B) seeker
(C) to seek
(D) seeking

110. The cash registers at Pirkle Books automatically calculate.
[A] calculate
[B] calculator
[C] calculating
[D] calculation

111. PaddleOCR can mix full-width option punctuation.
（A） first
(B） second
（C) third
（D） fourth
"""


def main():
    qs = parse_questions(SAMPLE)
    print(f"Parse được {len(qs)} câu hỏi")
    assert len(qs) == 8, f"Expected 8, got {len(qs)}"

    # Kiểm tra câu 1
    q1 = qs[0]
    assert q1.number == 1
    assert "Thủ đô" in q1.text
    assert q1.correct == "A"
    assert q1.options["A"] == "Hà Nội"
    print(f"  OK Câu 1: '{q1.text[:40]}...' → đáp án {q1.correct}")

    # Câu 2
    assert qs[1].correct == "B"
    assert qs[1].options["B"] == "4"

    # Câu 3 - dùng "Answer:"
    assert qs[2].correct == "A"

    # Câu 4 - đáp án dạng A)
    assert qs[3].correct == "A"
    assert "Hyper Text" in qs[3].options["A"]

    # Câu 5 - "Question N:"
    assert qs[4].correct == "B"

    # Câu 109 - đáp án dạng (A) (B) (C) (D)
    assert qs[5].number == 109
    assert qs[5].options["A"] == "seeks"
    assert qs[5].options["B"] == "seeker"

    # Câu 110 - đáp án dạng [A] [B] [C] [D]
    assert qs[6].number == 110
    assert qs[6].options["A"] == "calculate"

    # Câu 111 - PaddleOCR đôi khi trả dấu ngoặc full-width hoặc trộn hai loại.
    assert qs[7].number == 111
    assert qs[7].options == {
        "A": "first",
        "B": "second",
        "C": "third",
        "D": "fourth",
    }

    print("Tất cả test PASSED")


if __name__ == "__main__":
    main()


def test_part5_blank_and_glued_words_are_repaired():
    assert normalize_part5_question_text(
        "Proper maintenance of yourheating equipment ensures that smallissuescanbe "
        "fixed ... theybecomebigones."
    ) == (
        "Proper maintenance of your heating equipment ensures that small issues can be "
        "fixed _____ they become big ones."
    )
    assert normalize_part5_question_text(
        "Payments made .-- 400 P.M.. will be processed tomorrow."
    ) == "Payments made _____ 400 P.M.. will be processed tomorrow."
    assert normalize_part5_question_text(
        "This model is inexpensive. beautifullycrafted for daily use."
    ) == "This model is inexpensive _____ beautifully crafted for daily use."
    assert normalize_part5_question_text(
        "Mougey Fine Gifts isknown for its large rangeof goods."
    ) == "Mougey Fine Gifts is known for its large range of goods _____."
    assert normalize_part5_question_text(
        "Mr.Kim asked about theJasper account and thefactory location."
    ) == "Mr. Kim asked about the Jasper account and the factory location _____."
