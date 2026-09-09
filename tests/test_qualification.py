import pytest

from pcb_leads.qualification import (
    hardware_qualification,
    is_generic_company_name,
    resolve_company_name,
)


def test_generic_contact_title_falls_back_to_hostname_company_name():
    assert resolve_company_name("Contact", "https://acme-controls.de/contact", []) == "Acme Controls"


def test_generic_title_without_company_hostname_is_rejected():
    assert resolve_company_name(
        "Legal And Affiliate Disclaimer",
        "https://globalgrasshopper.com/legal",
        ["travel blog tourism affiliate"],
    ) == ""


def test_legal_company_name_has_priority_over_title_and_domain():
    text = "Impressum: ACME Sensors GmbH, Berlin, Germany"
    assert resolve_company_name("Contact", "https://acme-sensors.de", [text]) == "ACME Sensors GmbH"


def test_hostname_outranks_unverified_search_result_title():
    assert resolve_company_name(
        "Search Results: ACME Consulting",
        "https://acme-controls.de",
        ["Official company contact page."],
    ) == "Acme Controls"


def test_generic_company_names_are_detected_after_normalization():
    assert is_generic_company_name("  CONTACT  ") is True


def test_impressum_disclaimer_and_about_are_generic_company_names():
    for name in ("Impressum", "Disclaimer", "About"):
        assert is_generic_company_name(name) is True


def test_hardware_requires_two_distinct_signals():
    result = hardware_qualification(["We are an industrial sensor controller manufacturer."])
    assert result.eligible is True
    assert set(result.hardware_signals) >= {"industrial", "sensor", "controller"}


def test_search_query_alone_cannot_qualify_as_hardware_evidence():
    result = hardware_qualification(["industrial sensor controller manufacturer"])
    assert result.eligible is False


def test_single_hardware_word_is_not_enough():
    assert hardware_qualification(["A software platform for IoT analytics"]).eligible is False


def test_industrial_automation_consulting_is_not_hardware_qualified():
    result = hardware_qualification(["Industrial automation consulting services."])

    assert result.eligible is False
    assert set(result.hardware_signals) == {"industrial", "automation"}


def test_travel_affiliate_site_is_rejected_even_when_query_mentions_iot():
    result = hardware_qualification(["travel blog tourism affiliate guide", "IoT Gateway Norway"])
    assert result.eligible is False
    assert "travel/tourism" in result.exclusion_signals


def test_pcb_and_ems_peers_are_rejected():
    result = hardware_qualification(["PCB assembly EMS contract manufacturer"])
    assert result.eligible is False


def test_additional_pcb_ems_peer_phrases_override_hardware_signals():
    for phrase in (
        "pcb manufacturer",
        "printed circuit board manufacturer",
        "pcba manufacturer",
        "smt assembly services",
    ):
        result = hardware_qualification([f"Industrial sensor controller {phrase}."])

        assert result.eligible is False
        assert "PCB/EMS peer" in result.exclusion_signals


def test_non_generic_title_is_rejected_when_page_is_travel_affiliate_content():
    assert resolve_company_name(
        "Global Grasshopper",
        "https://globalgrasshopper.com/legal",
        ["travel blog tourism affiliate guide"],
    ) == ""


def test_travel_product_word_does_not_suppress_company_name_resolution():
    assert resolve_company_name(
        "Acme Power",
        "https://acme-power.example/products",
        ["We manufacture travel power adapters with embedded controllers."],
    ) == "Acme Power"


@pytest.mark.parametrize(
    ("text", "expected_exclusion"),
    [
        ("SaaS software platform for industrial automation analytics.", "software-only"),
        ("Industrial automation consulting services for manufacturers.", "consulting-only"),
        ("SEO and digital marketing agency for sensor controller brands.", "marketing/SEO"),
        ("Recruitment agency hiring sensor and controller engineers.", "recruitment"),
        ("Technology news publisher covering sensors and embedded controllers.", "news/media"),
        ("Supplier directory listing industrial sensors and controllers.", "directory operator"),
        ("Government agency procurement portal for sensors and controllers.", "government"),
        ("University education blog reviewing medical sensors and controllers.", "education blog"),
        ("Online fashion store selling sensor and controller themed shirts.", "unrelated ecommerce"),
        ("Travel tourism affiliate site reviewing sensor gadgets and controllers.", "travel/tourism"),
        ("PCB manufacturer offering sensor controller PCB assembly.", "PCB/EMS peer"),
    ],
)
def test_qualification_excludes_non_prospect_operator_categories(text, expected_exclusion):
    result = hardware_qualification([text])

    assert result.eligible is False
    assert expected_exclusion in result.exclusion_signals


@pytest.mark.parametrize(
    "text",
    [
        "Our sensor controller manufacturer publishes an engineering blog.",
        "Government-certified medical sensor and controller device manufacturer.",
        "Our embedded controller products include hardware and software tools.",
        "We manufacture industrial sensor and controller products and also provide design consulting.",
        "Recruitment of embedded engineers supports our sensor controller manufacturing.",
        "News: Acme launches a new industrial sensor controller product.",
        "University spinout manufacturing medical sensors and embedded controllers.",
        "Online store for our own industrial sensor and controller products.",
    ],
)
def test_qualification_exclusion_terms_do_not_match_legitimate_hardware_contexts(text):
    result = hardware_qualification([text])

    assert result.eligible is True
    assert result.exclusion_signals == ()


@pytest.mark.parametrize(
    ("text", "expected_exclusion"),
    [
        ("We build a cloud platform for industrial sensor controllers.", "software-only"),
        ("We produce cloud platform software for industrial sensor controllers.", "software-only"),
        ("Our cloud platform combines hardware and software analytics for industrial sensor controllers.", "software-only"),
        ("We build dashboards and provide consulting services for industrial sensor controllers.", "consulting-only"),
    ],
)
def test_generic_build_wording_does_not_bypass_software_or_consulting_identity(text, expected_exclusion):
    result = hardware_qualification([text])

    assert result.eligible is False
    assert expected_exclusion in result.exclusion_signals


@pytest.mark.parametrize(
    "text",
    [
        "We are a software company providing cloud analytics for our industrial sensor controllers.",
        "Acme is a software vendor. Our sensor and controller analytics run as a SaaS platform.",
        "Our device cloud platform analyzes industrial sensor and controller telemetry.",
        "We are a software company serving manufacturers of industrial sensor controllers.",
        "We are a software vendor whose customers design and manufacture industrial sensor controllers.",
    ],
)
def test_possessive_or_third_party_hardware_context_does_not_bypass_software_identity(text):
    result = hardware_qualification([text])

    assert result.eligible is False
    assert "software-only" in result.exclusion_signals


@pytest.mark.parametrize(
    "text",
    [
        "We manufacture industrial sensor controllers and provide software services.",
        "We design medical sensor devices and provide software services.",
        "We produce embedded controller equipment with a cloud platform.",
        "We sell industrial sensors and controller devices with software services.",
        "We are a manufacturer of industrial sensor controllers with software services.",
        "Our company handles the production of embedded controller devices with a software platform.",
        "Our company handles the sale of industrial sensors and controller devices with software services.",
    ],
)
def test_explicit_first_party_physical_business_actions_override_conditional_software_terms(text):
    result = hardware_qualification([text])

    assert result.eligible is True
    assert result.exclusion_signals == ()


@pytest.mark.parametrize(
    ("text", "company_name", "expected_exclusion"),
    [
        (
            "We're a software developer that manufactures industrial sensor controllers.",
            "Acme Controls",
            "software-only",
        ),
        (
            "We are a SaaS company and manufacture embedded controller devices.",
            "Acme Controls",
            "software-only",
        ),
        (
            "Our company is a consulting firm that designs medical sensor devices.",
            "Acme Controls",
            "consulting-only",
        ),
        (
            "We operate a cloud platform as our business and manufacture industrial sensors.",
            "Acme Controls",
            "software-only",
        ),
        (
            "Our company offers a cloud platform as its business and designs embedded controllers.",
            "Acme Controls",
            "software-only",
        ),
        (
            "Acme Controls is a software vendor and manufactures industrial sensor controllers.",
            "Acme Controls",
            "software-only",
        ),
        (
            "We manufacture industrial sensor controllers.",
            "Acme Software Company",
            "software-only",
        ),
        (
            "We manufacture industrial sensor controllers.",
            "Acme Software Vendor",
            "software-only",
        ),
        (
            "We manufacture industrial sensor controllers.",
            "Acme Software Developer",
            "software-only",
        ),
        (
            "We manufacture industrial sensor controllers.",
            "Acme Consulting Company",
            "consulting-only",
        ),
    ],
)
def test_explicit_software_cloud_saas_and_consulting_identities_always_exclude(
    text,
    company_name,
    expected_exclusion,
):
    result = hardware_qualification([text], company_name=company_name)

    assert result.eligible is False
    assert expected_exclusion in result.exclusion_signals


@pytest.mark.parametrize(
    "text",
    [
        "We sell our industrial sensor controller analytics as a cloud platform",
        "We offer our industrial sensor controller analytics as a cloud platform",
    ],
)
def test_cloud_platform_operator_identity_overrides_nearby_physical_action(text):
    result = hardware_qualification([text])

    assert result.eligible is False
    assert "software-only" in result.exclusion_signals


@pytest.mark.parametrize(
    "text",
    [
        "Our travel power adapter uses an embedded controller and power electronics.",
        "Our industrial sensor controller product was featured by Technology Magazine.",
        "Our industrial sensor controller product was featured by a travel magazine.",
        "Our industrial sensor controller product was featured by a travel publisher.",
        "Our industrial sensor controller product was featured in an online magazine.",
        "Our industrial sensor controller product was featured by an online magazine operator.",
    ],
)
def test_contextual_taxonomy_keeps_incidental_travel_and_magazine_mentions(text):
    result = hardware_qualification([text])

    assert result.eligible is True
    assert result.exclusion_signals == ()


@pytest.mark.parametrize(
    "text",
    [
        "Our industrial sensor controller was featured in an online magazine.",
        "Our travel agency customer uses our industrial sensor controller device.",
        "A software vendor featured our industrial sensor controller product.",
    ],
)
def test_contextual_operator_words_are_incidental_without_prospect_identity(text):
    result = hardware_qualification([text])

    assert result.eligible is True
    assert result.exclusion_signals == ()


@pytest.mark.parametrize(
    "text",
    [
        "We are a travel magazine covering industrial sensor controllers.",
        "Our company is a travel magazine covering industrial sensor controllers.",
    ],
)
def test_contextual_travel_magazine_operator_identity_is_excluded(text):
    result = hardware_qualification([text])

    assert result.eligible is False
    assert "travel/tourism" in result.exclusion_signals


@pytest.mark.parametrize(
    ("text", "expected_exclusion"),
    [
        ("We are a travel blog reviewing industrial sensors and controllers.", "travel/tourism"),
        ("This tourism site reviews industrial sensors and controllers.", "travel/tourism"),
        ("An online magazine publishing industrial sensor and controller reviews.", "news/media"),
        ("A news publisher covering embedded controllers and industrial sensors.", "news/media"),
        ("We are a software vendor for industrial sensor and controller analytics.", "software-only"),
        ("Acme is a software company for industrial sensor and controller analytics.", "software-only"),
        ("We are a travel agency reviewing industrial sensors and controllers.", "travel/tourism"),
        ("Acme is a travel blog covering industrial sensors and controllers.", "travel/tourism"),
        ("We are a tourism operator reviewing industrial sensors and controllers.", "travel/tourism"),
        ("Technology Magazine publishes industrial sensor and controller news.", "news/media"),
        ("We are a news publisher covering embedded controllers and industrial sensors.", "news/media"),
        ("We operate an online magazine covering industrial sensors and controllers.", "news/media"),
        ("We are an online magazine operator covering industrial sensors and controllers.", "news/media"),
        ("We're a travel agency reviewing industrial sensors and controllers.", "travel/tourism"),
        ("Our company operates a travel blog reviewing sensors and controllers.", "travel/tourism"),
        ("Atlas is a tourism operator reviewing industrial sensors and controllers.", "travel/tourism"),
        ("We're a news publication covering embedded controllers and industrial sensors.", "news/media"),
        ("Our company is a news publisher covering industrial sensors and controllers.", "news/media"),
        ("Our company operates an online magazine covering sensors and controllers.", "news/media"),
    ],
)
def test_contextual_taxonomy_excludes_nonhardware_business_identities(text, expected_exclusion):
    result = hardware_qualification([text])

    assert result.eligible is False
    assert expected_exclusion in result.exclusion_signals


@pytest.mark.parametrize(
    ("company_name", "expected_exclusion"),
    [
        ("Atlas Travel Agency", "travel/tourism"),
        ("Atlas Travel Blog", "travel/tourism"),
        ("Atlas Tourism Operator", "travel/tourism"),
        ("Circuit News Publication", "news/media"),
        ("Circuit News Publisher", "news/media"),
        ("Embedded Online Magazine", "news/media"),
        ("Technology Magazine", "news/media"),
    ],
)
def test_travel_and_media_company_name_identities_are_excluded(company_name, expected_exclusion):
    result = hardware_qualification(
        ["We manufacture industrial sensor controllers."],
        company_name=company_name,
    )

    assert result.eligible is False
    assert expected_exclusion in result.exclusion_signals
