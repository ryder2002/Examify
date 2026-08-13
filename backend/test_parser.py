"""Script test parser với text mẫu."""

from parser import parse_questions
import json

sample = """
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
"""

if __name__ == "__main__":
    questions = parse_questions(sample)
    print(f"Parse được {len(questions)} câu hỏi:")
    print(json.dumps([q.to_dict() for q in questions], ensure_ascii=False, indent=2))
