from sam.processors.matching import match_best, normalize_title


def test_normalize_title_basic() -> None:
    assert normalize_title("Dune: Part Two") == "dune part two"


def test_match_best_finds_candidate() -> None:
    candidates = ["The Last of Us", "Severance", "Dune: Part Two"]
    m = match_best("Just watched dune part 2 trailer!", candidates=candidates, min_score=0.6)
    assert m is not None
    assert m.candidate == "Dune: Part Two"
