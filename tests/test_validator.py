from outreach.validator import check_citations, validate_dm

EVIDENCE = [
    {"id": "bio", "kind": "bio", "text": "Building low-cost robot arms for SEA farms. Ex-NVIDIA."},
    {
        "id": "111",
        "kind": "post",
        "text": "We just open-sourced our sim2real grasping dataset — 40k episodes!",
    },
]


def test_valid_citation_passes():
    cites = [{"source_id": "111", "quote": "open-sourced our sim2real grasping dataset"}]
    ok, issues = check_citations(cites, EVIDENCE)
    assert ok == cites and issues == []


def test_quote_matching_ignores_case_whitespace_and_dash_variants():
    cites = [{"source_id": "111", "quote": "Open-sourced  our SIM2REAL grasping dataset - 40k"}]
    ok, _ = check_citations(cites, EVIDENCE)
    assert len(ok) == 1


def test_invented_quote_is_rejected():
    cites = [{"source_id": "111", "quote": "raised a seed round from Sequoia"}]
    ok, issues = check_citations(cites, EVIDENCE)
    assert ok == [] and "not found" in issues[0]


def test_unknown_source_is_rejected():
    ok, issues = check_citations([{"source_id": "999", "quote": "robot arms"}], EVIDENCE)
    assert ok == [] and issues


def test_good_dm_has_no_blocking_issues():
    text = (
        "Hi Mai, saw you open-sourced the sim2real grasping dataset (40k episodes). "
        "I run a small community for AI and robotics builders in Southeast Asia. "
        "Would you be open to a short chat about what you're building for SEA farms?"
    )
    cites = [{"source_id": "111", "quote": "open-sourced our sim2real grasping dataset"}]
    issues = validate_dm(text, cites, EVIDENCE, allowed_urls=[])
    assert [i for i in issues if i["level"] == "block"] == []


def test_false_familiarity_blocks():
    text = "Great to reconnect! As we discussed, we'd love to back your robot arms."
    cites = [{"source_id": "bio", "quote": "low-cost robot arms"}]
    issues = validate_dm(text, cites, EVIDENCE, allowed_urls=[])
    assert any(i["level"] == "block" and "familiarity" in i["msg"] for i in issues)


def test_placeholder_blocks():
    cites = [{"source_id": "bio", "quote": "low-cost robot arms"}]
    issues = validate_dm("Hi [Name], love your robot arms work.", cites, EVIDENCE, [])
    assert any(i["level"] == "block" and "placeholder" in i["msg"] for i in issues)


def test_no_valid_citation_blocks():
    issues = validate_dm("Hi, love your work on robots. Chat?", [], EVIDENCE, [])
    assert any(i["level"] == "block" and "evidence" in i["msg"] for i in issues)


def test_quoted_text_must_exist_in_evidence():
    text = 'Loved your line "robots will replace all farmers" — keen to chat about it.'
    cites = [{"source_id": "bio", "quote": "low-cost robot arms"}]
    issues = validate_dm(text, cites, EVIDENCE, [])
    assert any(i["level"] == "block" and "quoted" in i["msg"] for i in issues)


def test_unknown_url_blocks():
    cites = [{"source_id": "bio", "quote": "low-cost robot arms"}]
    issues = validate_dm(
        "Love the robot arms. See https://evil.example/x",
        cites,
        EVIDENCE,
        allowed_urls=["https://example.org"],
    )
    assert any(i["level"] == "block" and "link" in i["msg"] for i in issues)


def test_too_long_blocks():
    cites = [{"source_id": "bio", "quote": "low-cost robot arms"}]
    issues = validate_dm("robot arms " * 200, cites, EVIDENCE, [])
    assert any(i["level"] == "block" and "long" in i["msg"] for i in issues)
