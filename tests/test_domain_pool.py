from pathlib import Path

from domain_pool import DomainPool, DomainPoolRecord, import_seed_file


def record(domain: str = "acme.example") -> DomainPoolRecord:
    return DomainPoolRecord("ACME Controls", domain, f"https://{domain}", "Germany", "Industrial Electronics", "Exhibitor List", "https://source.example/acme", "industrial controller hardware")


def test_pool_appends_only_new_domains_and_tracks_check_state(tmp_path):
    pool = DomainPool(tmp_path / "company_domain_pool.xlsx")

    assert pool.add([record(), record("www.acme.example")]) == 1
    assert pool.metrics() == {"total": 1, "unchecked": 1, "email_found": 0}

    pool.mark_checked(["acme.example"], {"acme.example": "sales@acme.example"})
    pool.save()
    reloaded = DomainPool(pool.path)

    assert reloaded.metrics() == {"total": 1, "unchecked": 0, "email_found": 1}


def test_csv_import_keeps_existing_pool_rows_unchanged(tmp_path):
    pool = DomainPool(tmp_path / "company_domain_pool.xlsx")
    pool.add([record()])
    source = tmp_path / "domain_seed.csv"
    source.write_text("Company Name,Domain,Country,Industry\nACME,acme.example,Germany,Automation\nBeta,beta.example,UK,Sensor\n", encoding="utf-8")

    assert import_seed_file(pool, source) == 1
    pool.save()
    assert pool.metrics()["total"] == 2
