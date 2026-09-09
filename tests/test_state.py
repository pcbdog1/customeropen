from pcb_leads.state import RunState


def test_run_state_persists_seen_urls(tmp_path):
    path = tmp_path / "state.json"
    state = RunState.load(path)
    state.mark_url_seen("https://example.com")
    state.save()

    reloaded = RunState.load(path)
    assert reloaded.has_seen_url("https://example.com")
