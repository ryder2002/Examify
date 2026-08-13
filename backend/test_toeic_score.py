from toeic_score import scores


def test_exact_full_test_scales_match_the_supplied_tables():
    # The supplied tables diverge at the same raw score, so both values matter.
    assert scores(0, 100, 0, 100) == (5, 5, 10)
    assert scores(75, 100, 75, 100) == (385, 370, 755)
    assert scores(100, 100, 100, 100) == (495, 495, 990)


def test_partial_practice_is_explicitly_proportional_not_a_full_test_claim():
    assert scores(5, 10, 0, 0) == (260, 0, 260)
