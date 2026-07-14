"""Verify violation rules get populated titles from rule codes."""


def test_humanize_rule_code():
    from api.routes.violations import _humanize_rule_code
    assert _humanize_rule_code("slsa_build_build_service.exists") != ""
    assert "build" in _humanize_rule_code("slsa_build_build_service.exists").lower()


def test_humanize_handles_empty():
    from api.routes.violations import _humanize_rule_code
    assert _humanize_rule_code("") == ""
    assert _humanize_rule_code(None) == ""


def test_humanize_preserves_readable_input():
    from api.routes.violations import _humanize_rule_code
    result = _humanize_rule_code("cve_results_found.cve_results_found")
    assert result != ""


def test_humanize_expected_transformations():
    """Test specific transformations from rule codes to titles."""
    from api.routes.violations import _humanize_rule_code

    # slsa_build_build_service.exists -> "Slsa Build: Build Service Exists"
    assert _humanize_rule_code("slsa_build_build_service.exists") == "Slsa Build Build Service: Exists"

    # cve_results_found.cve_results_found -> "Cve Results Found: Cve Results Found"
    assert _humanize_rule_code("cve_results_found.cve_results_found") == "Cve Results Found: Cve Results Found"

    # source_image.exists -> "Source Image: Exists"
    assert _humanize_rule_code("source_image.exists") == "Source Image: Exists"
