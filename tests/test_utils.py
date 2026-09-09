import pytest

from pcb_leads import utils
from pcb_leads.utils import (
    dedupe_key,
    hosts_are_attributable,
    normalize_domain,
    normalize_mailbox,
)


def test_normalize_domain_removes_scheme_www_and_path():
    assert normalize_domain("https://www.example.de/products?x=1") == "example.de"


def test_dedupe_key_uses_domain_and_normalized_company_name():
    assert (
        dedupe_key("  ACME Robotics GmbH  ", "https://www.acme-robotics.de/")
        == "acme robotics gmbh|acme-robotics.de"
    )


def test_company_country_key_normalizes_both_components():
    assert utils.company_country_key(" Alpha Controls GmbH ", " Germany ") == "alpha controls gmbh|germany"


def test_registrable_domain_uses_bundled_public_and_private_suffix_rules():
    assert utils.registrable_domain("https://www.acme.co.uk/contact") == "acme.co.uk"
    assert utils.registrable_domain("shop.sensor.uk.com") == "sensor.uk.com"
    assert utils.registrable_domain("co.uk") == ""
    assert utils.registrable_domain("uk.com") == ""


def test_normalize_domain_never_returns_a_public_suffix_as_a_company_domain():
    assert normalize_domain("https://co.uk/contact") == ""


@pytest.mark.parametrize(
    "email",
    [
        "\u017fales@acme.co.uk",
        "\u0130nfo@acme.co.uk",
        "\u0131nfo@acme.co.uk",
        "\u212aontact@acme.co.uk",
        "sales@acme\u2024co.uk",
        "sales@acme\ufe52co.uk",
        "sales@b\u00fccher.de",
    ],
)
def test_normalize_mailbox_rejects_any_non_ascii_code_point_before_parsing(email):
    assert normalize_mailbox(email) == ""


@pytest.mark.parametrize(
    "email",
    [
        "sales@.acme.co.uk",
        "sales@acme.co.uk.",
        "sales@www.acme.co.uk",
        "sales@WWW.acme.co.uk",
        "sales@acme..co.uk",
        "sales@-acme.co.uk",
        "sales@acme-.co.uk",
        "sales@ac_me.co.uk",
    ],
)
def test_normalize_mailbox_rejects_raw_domain_labels_before_normalization(email):
    assert normalize_mailbox(email) == ""


def test_normalize_mailbox_accepts_ascii_punycode_domain():
    assert normalize_mailbox("Sales@xn--bcher-kva.de") == "sales@xn--bcher-kva.de"


def test_host_attribution_is_directional_and_never_accepts_siblings_or_suffixes():
    assert hosts_are_attributable("shop.acme.co.uk", "shop.acme.co.uk") is True
    assert hosts_are_attributable("acme.co.uk", "shop.acme.co.uk") is True
    assert hosts_are_attributable("mail.acme.co.uk", "acme.co.uk") is False
    assert hosts_are_attributable("mail.acme.co.uk", "shop.acme.co.uk") is False
    assert hosts_are_attributable("www.acme.co.uk", "shop.acme.co.uk") is False
    assert hosts_are_attributable("co.uk", "shop.acme.co.uk") is False
