"""Tests for slack_bot/parsing.py: splitting the text after /agent into a customer ID and a question."""

import pytest

from slack_bot.parsing import parse_message


def test_id_first_then_question():
    """The usual shape: the ID, then the question."""
    parsed = parse_message("CUST-1005 customer cannot activate roaming")
    assert parsed.customer_id == "CUST-1005"
    assert parsed.query == "customer cannot activate roaming"


def test_question_first_then_id():
    """The ID can come after the question."""
    parsed = parse_message("customer cannot activate roaming CUST-1005")
    assert parsed.customer_id == "CUST-1005"
    assert parsed.query == "customer cannot activate roaming"


def test_id_in_the_middle_leaves_a_clean_question():
    """Removing an ID from the middle must not leave a double space."""
    parsed = parse_message("customer CUST-1005 cannot activate roaming")
    assert parsed.query == "customer cannot activate roaming"


def test_id_is_case_insensitive_and_returned_upper_case():
    """A lower-case ID still matches, and is normalised to the form used in the data."""
    assert parse_message("cust-1005 roaming problem").customer_id == "CUST-1005"


@pytest.mark.parametrize("separator", [":", ",", " -", " –", ";"])
def test_separator_after_the_id_is_stripped(separator):
    """`CUST-1005: question` and similar are common; the separator is not part of the question."""
    assert parse_message(f"CUST-1005{separator} roaming problem").query == "roaming problem"


def test_missing_id_gives_none_and_keeps_the_text():
    """Without an ID the customer_id is None, so the bot can ask for one."""
    parsed = parse_message("customer cannot activate roaming")
    assert parsed.customer_id is None
    assert parsed.query == "customer cannot activate roaming"


def test_only_an_id_gives_an_empty_question():
    """An ID alone has no question, so the bot can ask for one."""
    parsed = parse_message("CUST-1005")
    assert parsed.customer_id == "CUST-1005"
    assert parsed.query == ""


def test_several_ids_use_the_first_and_keep_the_rest_in_the_question():
    """Only the first ID is the customer; a second one stays in the question text."""
    parsed = parse_message("CUST-1001 compare with CUST-1002")
    assert parsed.customer_id == "CUST-1001"
    assert "CUST-1002" in parsed.query


def test_empty_and_none_text_do_not_crash():
    """A blank message (or a missing text field) parses to nothing, not an exception."""
    assert parse_message("").customer_id is None
    assert parse_message(None).customer_id is None


def test_a_word_that_only_contains_cust_is_not_an_id():
    """`CUSTOMER-1` and `XCUST-1005` must not be read as customer IDs."""
    assert parse_message("CUSTOMER-1 is unhappy").customer_id is None
    assert parse_message("XCUST-1005 is unhappy").customer_id is None
