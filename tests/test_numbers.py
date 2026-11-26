from research_pipeline.numbers import number_found, numbers_in


def test_numbers_in_formats():
    assert numbers_in("28 % less, 1.37× faster, 1,200 tasks, 28\\% again") == ["28%", "1.37x", "1200"]


def test_numbers_in_skips_names_and_versions():
    assert numbers_in("GPT-5.5 and Llama-3 beat S-LoRA v2 in §5.1") == []


def test_number_found_exact_and_rounded():
    text = "It reduced time by 28.3% and ran 1.37x faster in 13.67 minutes over 1,200 runs."
    for n in ["28.3%", "28%", "1.4x", "1.37x", "13.7", "1200"]:
        assert number_found(n, text), n


def test_number_found_rejects_unit_and_form_changes():
    text = "It reduced time by 28% in 2000 ms; 13.8 versus 26.0 minutes."
    for n in ["0.28", "2", "2x", "29%", "27%"]:
        assert not number_found(n, text), n


def test_number_found_unparseable_is_false():
    assert not number_found("abc", "12 things")


def test_numbers_in_skips_unicode_hyphen_names():
    assert numbers_in("GPT\u20115.5 beats Llama\u20113 and Foo\u22122") == []


def test_number_found_survives_very_long_digit_runs():
    assert not number_found("5", "hash 123456789012345678901234567890 end")
    assert number_found("5", "hash 123456789012345678901234567890 and 5 items")


def test_numbers_in_plain_and_rounding_regressions():
    assert numbers_in("5 apples") == ["5"]
    assert number_found("28%", "27.5%")


def test_percent_words_count_as_percent_sign():
    assert number_found("80%", "About 80 per cent of the tokens")
    assert number_found("80%", "About 80 percent of the tokens")
    assert number_found("80%", "About 80 Percent of the tokens")
    assert number_found("80%", "About 80pct of the tokens")


def test_percent_word_needs_matching_sign():
    assert numbers_in("rose 14 to 22 per cent") == ["14", "22%"]
    assert not number_found("80", "80 per cent")


def test_percent_word_not_a_prefix_of_another_word():
    assert numbers_in("80 percentile") == ["80"]
