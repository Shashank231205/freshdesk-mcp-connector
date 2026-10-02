"""Tests for the evaluation grader. A wrong grader reports wrong accuracy."""

from evaluate_agent import Case, load_cases, normalise

NARROW_NBSP = chr(0x202F)
NB_HYPHEN = chr(0x2011)
NBSP = chr(0x00A0)


def test_normalise_folds_typographic_spaces_and_hyphens() -> None:
    raw = (
        f"Raised by **Aarav{NARROW_NBSP}Mehta**, order ORD{NB_HYPHEN}1001,{NBSP} 5{NB_HYPHEN}7 days"
    )

    assert normalise(raw) == "raised by aarav mehta, order ord-1001, 5-7 days"


def test_case_passes_when_every_check_holds() -> None:
    case = Case(
        id="x",
        question="q",
        must_include=("aarav mehta",),
        must_include_any=("2 ", "two"),
        must_not_include=("sneha",),
    )

    assert case.problems(f"Two tickets, both from Aarav{NARROW_NBSP}Mehta.") == []


def test_case_reports_each_failed_check() -> None:
    case = Case(id="x", question="q", must_include=("kabir",), must_not_include=("#",))

    assert case.problems("Ticket #20 belongs to contact 1130009701353.") == [
        "missing 'kabir'",
        "contains '#'",
    ]


def test_missing_answer_fails() -> None:
    assert Case(id="x", question="q").problems(None) == ["no final answer"]


def test_shipped_cases_are_well_formed() -> None:
    cases = load_cases()

    assert len({c.id for c in cases}) == len(cases), "case ids must be unique"
    assert all(c.must_include or c.must_include_any for c in cases)
    assert all(s == normalise(s) for c in cases for s in c.must_include + c.must_not_include)
