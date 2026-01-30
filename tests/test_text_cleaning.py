from sam.processors.text_cleaning import clean_text_for_nlp


def test_clean_text_strips_urls_and_collapses_whitespace() -> None:
    text = "hello   https://example.com   world"
    assert clean_text_for_nlp(text) == "hello world"


def test_clean_text_unwraps_markdown_links() -> None:
    text = "see [docs](https://example.com/docs) now"
    assert clean_text_for_nlp(text) == "see docs now"


def test_clean_text_strips_reddit_noise() -> None:
    text = "&gt; quoted line\nI loved /r/movies and /u/someone"
    assert clean_text_for_nlp(text) == "I loved and"
