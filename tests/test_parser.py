from pcb_leads.parser import (
    extract_contact_candidates,
    extract_public_emails,
    extract_phones,
    extract_title,
)


def test_extract_public_emails_prefers_business_role_addresses():
    html = """
    Contact sales@robotics.example and info@robotics.example.
    Ignore jane.doe@yahoo.com and hidden [at] example [dot] com.
    """
    assert extract_public_emails(html) == ["sales@robotics.example", "info@robotics.example", "jane.doe@yahoo.com"]


def test_extract_contact_candidates_classifies_generic_and_public_work_email():
    html = """
    Contact jane.smith@robotics.example or procurement@robotics.example.
    Personal: jane.smith@yahoo.com
    """
    contacts = extract_contact_candidates(html, "https://www.robotics.example/team")
    assert [(contact.email, contact.email_type) for contact in contacts] == [
        ("procurement@robotics.example", "Generic Company Email"),
        ("jane.smith@robotics.example", "Public Work Email"),
        ("jane.smith@yahoo.com", "Private Email"),
    ]


def test_extract_contact_candidates_allows_free_email_only_on_company_page_with_business_role():
    html = "Business contact: sales@yahoo.com. Personal founder email: jane.doe@yahoo.com"
    contacts = extract_contact_candidates(html, "https://company.example/contact")
    assert [(contact.email, contact.email_type) for contact in contacts] == [
        ("sales@yahoo.com", "Generic Company Email"),
        ("jane.doe@yahoo.com", "Private Email"),
    ]


def test_extract_contact_candidates_allows_third_party_public_email_on_company_page():
    html = "Contact info@vendor.example for unrelated support and info@robotics.example."
    contacts = extract_contact_candidates(html, "https://robotics.example/impressum")
    assert [(contact.email, contact.email_type) for contact in contacts] == [
        ("info@robotics.example", "Generic Company Email"),
        ("info@vendor.example", "Generic Company Email"),
    ]


def test_extract_contact_candidates_detects_contact_form_when_no_email():
    html = '<a href="/contact">Contact Form</a><a href="/impressum">Impressum</a>'
    contacts = extract_contact_candidates(html, "https://robotics.example")
    assert contacts[0].email == "Not Found"
    assert contacts[0].email_type == "Contact Form Only"
    assert contacts[0].contact_method == "Website Contact Form"
    assert contacts[0].contact_form_url == "https://robotics.example/contact"


def test_extract_phones_returns_public_looking_numbers():
    html = "Call us at +49 30 1234 5678 or fax +49 89 2222 3333."
    assert "+49 30 1234 5678" in extract_phones(html, "DE")


def test_extract_title_from_html_title_tag():
    html = "<html><head><title>ACME Robotics - Industrial Automation</title></head></html>"
    assert extract_title(html) == "ACME Robotics - Industrial Automation"


def test_extract_contact_candidates_skips_image_filename_false_email():
    html = '<img src="/assets/mail@3x-s29j7u-640x0.png">Contact info@robotics.example'
    contacts = extract_contact_candidates(html, "https://robotics.example/contact")

    assert [(contact.email, contact.email_type) for contact in contacts] == [
        ("info@robotics.example", "Generic Company Email")
    ]


def test_extract_contact_candidates_skips_wix_tracking_email():
    html = "Contact 605a7baede844d278b89dc95ae0a9123@sentry-next.wixpress.com or info@robotics.example"

    contacts = extract_contact_candidates(html, "https://robotics.example/contact")

    assert [(contact.email, contact.email_type) for contact in contacts] == [
        ("info@robotics.example", "Generic Company Email")
    ]


def test_extract_contact_candidates_skips_placeholder_email():
    contacts = extract_contact_candidates(
        (
            "Examples: example@domain.com, johnsmith@example.com, and name@example.org. "
            "Contact: sales@robotics.example"
        ),
        "https://robotics.example/contact",
    )

    assert [(contact.email, contact.email_type) for contact in contacts] == [
        ("sales@robotics.example", "Generic Company Email")
    ]
