"""TOEIC Listening/Reading score conversion used by results and history.

The tables below transcribe the two official-style conversion charts supplied
for Examify.  They are intentionally explicit, rather than a percentage
formula: a 75/100 Listening result is 385, while Reading is 370.
"""

from __future__ import annotations


LISTENING_SCALE = (
    5, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90,
    95, 100, 105, 110, 115, 120, 125, 130, 135, 140, 145, 150, 155, 160,
    165, 170, 175, 180, 185, 190, 195, 200, 205, 210, 215, 220, 225, 230,
    235, 240, 245, 250, 255, 260, 265, 270, 275, 280, 285, 290, 295, 300,
    305, 310, 315, 320, 325, 330, 335, 340, 345, 350, 355, 360, 365, 370,
    375, 380, 385, 395, 400, 405, 410, 415, 420, 425, 430, 435, 440, 445,
    450, 455, 460, 465, 470, 475, 480, 485, 490, 495, 495, 495, 495, 495,
)

READING_SCALE = (
    5, 5, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80,
    85, 90, 95, 100, 105, 110, 115, 120, 125, 130, 135, 140, 145, 150,
    155, 160, 165, 170, 175, 180, 185, 190, 195, 200, 205, 210, 215, 220,
    225, 230, 235, 240, 245, 250, 255, 260, 265, 270, 275, 280, 285, 290,
    295, 300, 305, 310, 315, 320, 325, 330, 335, 340, 345, 350, 355, 360,
    365, 370, 375, 380, 385, 390, 395, 400, 405, 410, 415, 420, 425, 430,
    435, 440, 445, 450, 455, 460, 465, 470, 475, 480, 485, 490, 495,
)


def section_score(correct: int, total: int, scale: tuple[int, ...]) -> int:
    """Convert one section, exactly for a 100-question TOEIC section.

    Short practice selections have no official raw-score conversion. They use
    their proportional 0–100 raw equivalent only as an estimate.
    """
    if total <= 0:
        return 0
    raw = max(0, min(100, correct if total == 100 else round(correct * 100 / total)))
    return scale[raw]


def scores(
    listening_correct: int,
    listening_total: int,
    reading_correct: int,
    reading_total: int,
) -> tuple[int, int, int]:
    listening = section_score(listening_correct, listening_total, LISTENING_SCALE)
    reading = section_score(reading_correct, reading_total, READING_SCALE)
    return listening, reading, listening + reading
