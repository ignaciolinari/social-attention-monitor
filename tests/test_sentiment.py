from sam.processors.sentiment import analyze_sentiment


def test_sentiment_empty_text_neutral() -> None:
    r = analyze_sentiment(" ")
    assert r.label == "neutral"
    assert r.compound == 0.0


def test_sentiment_positive_text() -> None:
    r = analyze_sentiment("this is amazing and wonderful")
    assert r.compound > 0
