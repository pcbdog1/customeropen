from email_enrichment import HunterEmailEnricher
from search_provider import StableSearchProvider


def test_local_seed_provider_is_offline_and_reports_unconfigured(monkeypatch):
    monkeypatch.setattr("search_provider.load_project_env", lambda: None)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setenv("SEARCH_PROVIDER", "local_seed")

    provider = StableSearchProvider()

    assert not provider.configured
    assert provider.search("industrial electronics exhibitor", 5) == []
    assert provider.status()["provider"] == "local_seed"


def test_hunter_is_disabled_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    monkeypatch.setenv("EMAIL_ENRICHMENT_ENABLED", "False")

    enricher = HunterEmailEnricher()

    assert not enricher.configured
    assert enricher.find_public_business_email("acme.example") == ""


def test_auto_provider_falls_through_after_request_failure(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "auto")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-test")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-test")
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    class FailedResponse:
        def raise_for_status(self):
            raise requests.HTTPError("quota")

    class TavilyResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"results": [{"title": "ACME", "url": "https://acme.example", "content": "sensor hardware"}]}

    import requests
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: FailedResponse())
    monkeypatch.setattr(requests, "post", lambda url, *args, **kwargs: TavilyResponse() if "tavily" in url else FailedResponse())

    provider = StableSearchProvider()
    results = provider.search("sensor manufacturer Germany", 5)

    assert [item.url for item in results] == ["https://acme.example"]
    assert provider.usage_summary()["brave"]["requests"] == 1
    assert provider.usage_summary()["tavily"]["requests"] == 1
    assert provider.usage_summary()["brave"]["failure_reason"]
