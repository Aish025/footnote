"""Unit tests for Q&A normalisation and ASIN mapping in scripts/check_qa_overlap.py."""

import ast

from check_qa_overlap import asin_to_product, qa_pairs

LINE_2016 = (
    "{'questionType': 'yes/no', 'asin': 'B001', 'answerTime': 'Mar 8, 2014', "
    "'unixTime': 1394265600, 'question': 'Is there a SIM card?', 'answerType': 'Y', "
    "'answer': 'Yes.'}"
)
LINE_2019 = (
    "{'asin': 'B002', 'questions': [{'questionType': 'open-ended', 'askerID': u'A1', "
    "'questionTime': u'November 20, 2013', 'questionText': u'Works with a Pixel?', "
    "'answers': [{'answerText': u'Yes', 'answerTime': u'Nov 21, 2013'}, "
    "{'answerText': u'No, wrong port', 'answerTime': u'Dec 1, 2013'}]}]}"
)


def test_2016_line_gives_one_pair() -> None:
    [pair] = list(qa_pairs(ast.literal_eval(LINE_2016), "qa_2016"))
    assert pair == {
        "asin": "B001",
        "question": "Is there a SIM card?",
        "answer": "Yes.",
        "question_type": "yes/no",
        "answered_on": "Mar 8, 2014",
    }


def test_2019_line_gives_one_pair_per_answer() -> None:
    pairs = list(qa_pairs(ast.literal_eval(LINE_2019), "qa_2019"))
    assert [p["answer"] for p in pairs] == ["Yes", "No, wrong port"]
    assert {p["question"] for p in pairs} == {"Works with a Pixel?"}


def test_asin_mapping_covers_parent_and_variants() -> None:
    mapping = asin_to_product({"P1"}, {"V1": "P1", "V2": "P1"})
    assert mapping == {"P1": "P1", "V1": "P1", "V2": "P1"}
