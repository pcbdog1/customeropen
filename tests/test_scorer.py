from pcb_leads.scorer import score_candidate


def test_score_five_for_hardware_product_and_hiring_signal():
    score, reason = score_candidate(
        text="robotics controller hardware embedded engineer electronics PCB firmware",
        industry="Robotics",
        has_contact=True,
    )
    assert score == 5
    assert "direct signal" in reason


def test_score_four_for_clear_hardware_without_direct_signal():
    score, reason = score_candidate(
        text="industrial sensor device control board electronics",
        industry="Industrial Automation",
        has_contact=False,
    )
    assert score == 4
    assert "hardware product" in reason


def test_score_three_for_target_industry_with_weak_evidence():
    score, reason = score_candidate(
        text="automation consulting and integration services",
        industry="Automation",
        has_contact=False,
    )
    assert score == 3
    assert "target industry" in reason


def test_score_four_for_niche_hardware_from_external_library():
    score, reason = score_candidate(
        text="EEG headset and neural recording system for brain computer interface research",
        industry="Neurotechnology and Brain Computer Interface",
        has_contact=True,
    )

    assert score == 4
    assert "hardware product" in reason


def test_score_five_for_library_job_title_on_niche_hardware():
    score, reason = score_candidate(
        text="Quantum control electronics team is hiring an NPI Engineer",
        industry="Research Quantum Cryogenic and High Voltage",
        has_contact=True,
    )

    assert score == 5
    assert "direct signal" in reason
