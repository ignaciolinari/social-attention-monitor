from sam.processors.matching import match_best, normalize_title


def test_normalize_title_basic() -> None:
    assert normalize_title("Dune: Part Two") == "dune part two"


def test_match_best_finds_candidate() -> None:
    candidates = ["The Last of Us", "Severance", "Dune: Part Two"]
    m = match_best("Just watched dune part 2 trailer!", candidates=candidates, min_score=0.6)
    assert m is not None
    assert m.candidate == "Dune: Part Two"


def test_alias_matching_uses_token_boundaries() -> None:
    candidates = ["Game of Thrones", "Severance", "The White Lotus"]
    assert match_best("I forgot this show name", candidates=candidates, min_score=0.95) is None
    assert (
        match_best("Several episodes dropped today", candidates=candidates, min_score=0.95) is None
    )


def test_alias_matching_keeps_expected_shortcuts() -> None:
    candidates = ["Game of Thrones", "The White Lotus"]
    got = match_best("watching got finale", candidates=candidates, min_score=0.95)
    assert got is not None
    assert got.candidate == "Game of Thrones"

    wl = match_best("new wl trailer", candidates=candidates, min_score=0.95)
    assert wl is not None
    assert wl.candidate == "The White Lotus"
