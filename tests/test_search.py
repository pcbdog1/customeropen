from pcb_leads.search import (
    BingHtmlSearch,
    BingWebSearchApi,
    DuckDuckGoHtmlSearch,
    FallbackSearchProvider,
    GoogleHtmlSearch,
    MojeekHtmlSearch,
    MultiSearchProvider,
    SearchProvider,
    YahooHtmlSearch,
    YandexHtmlSearch,
    default_search_provider,
)


class EmptyProvider(SearchProvider):
    def search(self, query: str, limit: int):
        return []


class FailingProvider(SearchProvider):
    def search(self, query: str, limit: int):
        raise RuntimeError("remote closed connection")


def test_fallback_search_provider_returns_no_bundled_company_records():
    provider = FallbackSearchProvider(EmptyProvider())
    results = provider.search("hardware engineer robotics company Germany electronics", 3)
    assert results == []


def test_fallback_search_provider_handles_primary_errors_without_private_seeds():
    provider = FallbackSearchProvider(FailingProvider())
    results = provider.search("embedded engineer automation company Germany", 2)
    assert results == []


class FakeResponse:
    status_code = 200
    text = """
    <html><body>
      <li class="b_algo">
        <h2><a href="https://acme.example/products">ACME Industrial Cameras</a></h2>
        <div class="b_caption"><p>Industrial camera and machine vision hardware manufacturer.</p></div>
      </li>
    </body></html>
    """

    def raise_for_status(self):
        return None


class FakeSession:
    def get(self, *args, **kwargs):
        return FakeResponse()


def test_bing_html_search_parses_public_web_results():
    provider = BingHtmlSearch("test-agent", timeout=2, pause_seconds=0)
    provider.session = FakeSession()

    results = provider.search("USA industrial camera manufacturer", 3)

    assert len(results) == 1
    assert results[0].title == "ACME Industrial Cameras"
    assert results[0].url == "https://acme.example/products"
    assert "machine vision" in results[0].snippet


def test_default_search_without_api_prefers_duckduckgo_before_blocked_html_providers(monkeypatch):
    monkeypatch.delenv("BING_SEARCH_API_KEY", raising=False)

    provider = default_search_provider("test-agent", timeout=12)

    assert isinstance(provider, FallbackSearchProvider)
    assert isinstance(provider.primary, MultiSearchProvider)
    assert [type(item) for item in provider.primary.providers] == [
        DuckDuckGoHtmlSearch,
        YahooHtmlSearch,
        BingHtmlSearch,
        YandexHtmlSearch,
        MojeekHtmlSearch,
        GoogleHtmlSearch,
    ]


def test_default_search_with_api_keeps_bing_api_precedence(monkeypatch):
    monkeypatch.setenv("BING_SEARCH_API_KEY", "test-key")

    provider = default_search_provider("test-agent", timeout=12)

    assert isinstance(provider, FallbackSearchProvider)
    assert isinstance(provider.primary, MultiSearchProvider)
    assert isinstance(provider.primary.providers[0], BingWebSearchApi)
    assert isinstance(provider.primary.providers[1], DuckDuckGoHtmlSearch)


def test_multi_search_uses_first_provider_on_every_successive_query():
    calls = []

    class RecordingProvider(SearchProvider):
        def __init__(self, name):
            self.name = name

        def search(self, query, limit):
            calls.append((self.name, query))
            return [type("Result", (), {"url": f"https://{self.name}.example"})()]

    first = RecordingProvider("first")
    second = RecordingProvider("second")
    provider = MultiSearchProvider([first, second])

    assert provider.search("one", 1)[0].url == "https://first.example"
    assert provider.search("two", 1)[0].url == "https://first.example"
    assert calls == [("first", "one"), ("first", "two")]


def test_multi_search_uses_configured_fallback_order_on_every_successive_query():
    calls = []

    class RecordingProvider(SearchProvider):
        def __init__(self, name, has_result):
            self.name = name
            self.has_result = has_result

        def search(self, query, limit):
            calls.append((self.name, query))
            if not self.has_result:
                return []
            return [type("Result", (), {"url": f"https://{self.name}.example"})()]

    provider = MultiSearchProvider(
        [RecordingProvider("first", False), RecordingProvider("second", True)]
    )

    assert provider.search("one", 1)[0].url == "https://second.example"
    assert provider.search("two", 1)[0].url == "https://second.example"
    assert calls == [
        ("first", "one"),
        ("second", "one"),
        ("first", "two"),
        ("second", "two"),
    ]


def test_multi_search_circuit_breaks_after_two_exceptions_but_not_empty_results():
    calls = []

    class NamedProvider(SearchProvider):
        def __init__(self, name, outcome):
            self.name = name
            self.outcome = outcome

        def search(self, query, limit):
            calls.append(self.name)
            if self.outcome == "raise":
                raise TimeoutError("provider timeout")
            if self.outcome == "empty":
                return []
            return [
                type(
                    "Result",
                    (),
                    {
                        "title": "Sensor result",
                        "snippet": "Industrial sensor controller",
                        "url": "https://success.example",
                    },
                )()
            ]

    provider = MultiSearchProvider(
        [
            NamedProvider("failing", "raise"),
            NamedProvider("empty", "empty"),
            NamedProvider("success", "success"),
        ]
    )

    for _ in range(3):
        assert provider.search("industrial sensor", 1)

    diagnostics = {item["name"]: item for item in provider.provider_diagnostics}
    assert calls.count("failing") == 2
    assert calls.count("empty") == 3
    assert provider.provider_attempts == 8
    assert provider.provider_failures == 2
    assert diagnostics["NamedProvider[0]"]["circuit_open"] is True
    assert diagnostics["NamedProvider[1]"]["failures"] == 0


def test_multi_search_hard_bounds_actual_provider_attempts():
    class CountingEmptyProvider(SearchProvider):
        def __init__(self):
            self.calls = 0

        def search(self, query, limit):
            self.calls += 1
            return []

    first = CountingEmptyProvider()
    second = CountingEmptyProvider()
    provider = MultiSearchProvider([first, second])
    provider.configure_attempt_budget(5)

    for _ in range(10):
        provider.search("industrial sensor", 1)

    assert provider.provider_attempts == 5
    assert provider.provider_failures == 0
    assert first.calls + second.calls == 5


def test_bing_html_search_decodes_redirect_and_rejects_map_noise():
    encoded = (
        "https://www.bing.com/ck/a?u="
        "a1aHR0cHM6Ly9hY21lLWNhbWVyYXMuZXhhbXBsZS9wcm9kdWN0cw"
    )

    assert BingHtmlSearch._clean_bing_url(encoded) == "https://acme-cameras.example/products"
    assert BingHtmlSearch._is_noise_url("https://maps.google.com/place/acme") is True


def test_yahoo_html_search_decodes_result_url():
    encoded = (
        "https://r.search.yahoo.com/path/RU="
        "https%3a%2f%2facme-cameras.example%2fproducts/RK=2/RS=abc"
    )

    assert YahooHtmlSearch._clean_yahoo_url(encoded) == "https://acme-cameras.example/products"
