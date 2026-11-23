from app_research_agent import research
from app_research_agent.apps import App
from app_research_agent.research import crawl_candidates, docs_seeds, is_official, official_domains
from app_research_agent.web import Page

SHERLOCK = App(58, "Data", "Sherlock", "github.com/sherlock-project/sherlock")
AMAZON = App(49, "Ecom", "Amazon Selling Partner", "developer-docs.amazon.com/sp-api")
ECWID = App(47, "Ecom", "Ecwid", "ecwid.com")


def fake_fetch(redirects):
    def f(url, refresh=False):
        return Page(url=url, final_url=redirects.get(url, url), status=200, text="x" * 300)

    return f


def test_code_host_scoped_to_repo_owner():
    assert docs_seeds(SHERLOCK) == ["https://github.com/sherlock-project/sherlock"]
    assert is_official(
        SHERLOCK, "https://github.com/sherlock-project/sherlock/blob/master/README.md"
    )
    assert not is_official(SHERLOCK, "https://docs.github.com/en/rest")


def test_redirect_target_counts_as_official(monkeypatch):
    official_domains.cache_clear()
    monkeypatch.setattr(
        research.web,
        "fetch",
        fake_fetch(
            {"https://developer-docs.amazon.com/sp-api": "https://developer-docs.amazon/sp-api"}
        ),
    )
    assert is_official(AMAZON, "https://developer-docs.amazon/sp-api/docs/self-authorization.md")
    assert not is_official(AMAZON, "https://developer.alexa.example/x")
    # a hint with a path does not fan out to developer./docs. hosts
    assert not any("developers.amazon.com" in s for s in docs_seeds(AMAZON))
    official_domains.cache_clear()


def test_bare_domain_probes_docs_hosts_and_crawl_picks_topics(monkeypatch):
    official_domains.cache_clear()
    monkeypatch.setattr(research.web, "fetch", fake_fetch({}))
    seeds = docs_seeds(ECWID)
    assert "https://docs.ecwid.com" in seeds and "https://docs.ecwid.com/llms.txt" in seeds
    page = Page(
        url="https://docs.ecwid.com",
        status=200,
        text="x" * 300,
        links=[
            ["https://docs.ecwid.com/changelog/oauth-change", "OAuth changed"],
            ["https://docs.ecwid.com/api-reference/authentication", "Authentication"],
            [
                "https://docs.ecwid.com/get-started/set-up-your-dev-environment",
                "Set up dev environment",
            ],
            ["https://zapier.com/apps/ecwid", "Zapier"],
        ],
    )
    picked = crawl_candidates(ECWID, [page])
    assert "https://docs.ecwid.com/api-reference/authentication" in picked
    assert "https://docs.ecwid.com/get-started/set-up-your-dev-environment" in picked
    assert not any("changelog" in u or "zapier" in u for u in picked)
    official_domains.cache_clear()


GORGIAS = App(19, "Support", "Gorgias", "developers.gorgias.com")


def test_topic_picks_round_robin_before_second_picks(monkeypatch):
    official_domains.cache_clear()
    monkeypatch.setattr(research.web, "fetch", fake_fetch({}))
    page = Page(
        url="https://developers.gorgias.com",
        status=200,
        text="x" * 300,
        links=[
            ["https://developers.gorgias.com/docs/oauth2-authentication", "OAuth2"],
            ["https://developers.gorgias.com/docs/oauth2-scopes", "OAuth2 scopes"],
            ["https://developers.gorgias.com/docs/developer-sandbox", "Sandbox"],
            ["https://developers.gorgias.com/docs/mcp", "MCP server"],
        ],
    )
    first_three = set(crawl_candidates(GORGIAS, [page])[:3])
    assert "https://developers.gorgias.com/docs/developer-sandbox" in first_three
    assert "https://developers.gorgias.com/docs/mcp" in first_three
    official_domains.cache_clear()


def test_vendor_github_org_is_official(monkeypatch):
    official_domains.cache_clear()
    monkeypatch.setattr(research.web, "fetch", fake_fetch({}))
    assert is_official(GORGIAS, "https://github.com/gorgias/mcp-cookbook")
    assert not is_official(GORGIAS, "https://github.com/someone/helpdesk-scraper")
    official_domains.cache_clear()


def test_code_host_project_rejects_same_name_sites_and_docs_github_is_not_a_repo(monkeypatch):
    official_domains.cache_clear()
    monkeypatch.setattr(research.web, "fetch", fake_fetch({}))
    assert not is_official(SHERLOCK, "https://docs.sherlocklabs.dev/explore/mcp")
    gh = App(61, "Dev", "GitHub", "docs.github.com/rest")
    assert not research._is_code_host(gh)
    assert is_official(gh, "https://docs.github.com/en/rest/authentication")
    official_domains.cache_clear()
