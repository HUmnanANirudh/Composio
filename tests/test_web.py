from app_research_agent.web import _walk_hits, registrable, relevant_excerpt


def test_walk_hits_nested_and_json_strings():
    data = {
        "data": {
            "results": '[{"link": "https://a.com/x", "title": "A"}]',
            "more": [
                {"url": "https://b.com", "snippet": "b"},
                {"url": "https://a.com/x"},
            ],
        }
    }
    hits = _walk_hits(data, "t")
    assert [h.url for h in hits] == ["https://b.com", "https://a.com/x"] or [
        h.url for h in hits
    ] == ["https://a.com/x", "https://b.com"]
    assert len(hits) == 2


def test_registrable():
    assert registrable("developers.klaviyo.com") == "klaviyo.com"
    assert registrable("api.example.co.uk") == "example.co.uk"


def test_excerpt_prefers_auth_paragraphs():
    text = (
        "Intro about the company.\n\n" * 50
        + "Authenticate with an OAuth access token or API key.\n\n"
    )
    ex = relevant_excerpt(text, 200)
    assert "OAuth" in ex


def test_excerpt_fallback_when_paragraphs_too_big():
    assert relevant_excerpt("x" * 5000, 100) == "x" * 100


def test_atomic_write_replaces_without_leftovers(tmp_path):
    from app_research_agent.config import atomic_write

    p = tmp_path / "a" / "rec.json"
    atomic_write(p, "one")
    atomic_write(p, "two")
    assert p.read_text() == "two"
    assert [f.name for f in p.parent.iterdir()] == ["rec.json"]
