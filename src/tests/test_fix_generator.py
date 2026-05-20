"""Tests for deterministic fixer functions in fix_generator.py."""

import json
from unittest.mock import MagicMock, patch

from fixers.fix_generator import (
    apply_hermetic_fix,
    apply_sbom_vendor_label_fix,
    apply_rpm_repo_id_fix,
    find_floating_bundle_refs,
    parse_deprecated_task_fixes,
    parse_untrusted_image_refs,
    _refresh_pinned_ref,
    resolve_quay_digest,
)


# ---------------------------------------------------------------------------
# apply_hermetic_fix
# ---------------------------------------------------------------------------

def test_hermetic_fix_unquoted_false():
    content = "- name: hermetic\n  value: false\n"
    assert apply_hermetic_fix(content) == '- name: hermetic\n  value: "true"\n'


def test_hermetic_fix_quoted_false():
    content = '- name: hermetic\n  value: "false"\n'
    assert apply_hermetic_fix(content) == '- name: hermetic\n  value: "true"\n'


def test_hermetic_fix_default_keyword():
    content = "- name: hermetic\n  default: false\n"
    assert apply_hermetic_fix(content) == '- name: hermetic\n  default: "true"\n'


def test_hermetic_fix_already_true():
    content = '- name: hermetic\n  value: "true"\n'
    assert apply_hermetic_fix(content) == content


def test_hermetic_fix_absent_param():
    content = "- name: git-url\n  value: https://github.com/org/repo\n"
    assert apply_hermetic_fix(content) == content


def test_hermetic_fix_indentation_variants():
    content = "    - name: hermetic\n        value: false\n"
    result = apply_hermetic_fix(content)
    assert '"true"' in result
    assert 'false' not in result


# ---------------------------------------------------------------------------
# apply_sbom_vendor_label_fix
# ---------------------------------------------------------------------------

def test_vendor_label_already_present():
    content = 'FROM ubi9\nLABEL vendor="Red Hat, Inc."\nCMD ["bash"]\n'
    assert apply_sbom_vendor_label_fix(content) == content


def test_vendor_label_inserted_after_last_label():
    content = "FROM ubi9\nLABEL name=myapp\nLABEL version=1.0\nCMD [\"bash\"]\n"
    result = apply_sbom_vendor_label_fix(content)
    lines = result.splitlines()
    vendor_idx = next(i for i, ln in enumerate(lines) if 'vendor' in ln)
    version_idx = next(i for i, ln in enumerate(lines) if 'version=1.0' in ln)
    assert vendor_idx == version_idx + 1


def test_vendor_label_inserted_before_cmd_when_no_labels():
    content = "FROM ubi9\nRUN dnf install -y git\nCMD [\"bash\"]\n"
    result = apply_sbom_vendor_label_fix(content)
    lines = result.splitlines()
    vendor_idx = next(i for i, ln in enumerate(lines) if 'vendor' in ln)
    cmd_idx = next(i for i, ln in enumerate(lines) if ln.startswith('CMD'))
    assert vendor_idx < cmd_idx


def test_vendor_label_inserted_before_entrypoint():
    content = "FROM ubi9\nRUN echo hi\nENTRYPOINT [\"/start.sh\"]\n"
    result = apply_sbom_vendor_label_fix(content)
    lines = result.splitlines()
    vendor_idx = next(i for i, ln in enumerate(lines) if 'vendor' in ln)
    entry_idx = next(i for i, ln in enumerate(lines) if ln.startswith('ENTRYPOINT'))
    assert vendor_idx < entry_idx


def test_vendor_label_appended_when_no_label_or_cmd():
    content = "FROM ubi9\nRUN dnf install -y git\n"
    result = apply_sbom_vendor_label_fix(content)
    assert result.endswith('LABEL vendor="Red Hat, Inc."\n')


def test_vendor_label_idempotent_whitespace_forms():
    content = 'FROM ubi9\nLABEL vendor = "Red Hat, Inc."\n'
    assert apply_sbom_vendor_label_fix(content) == content


def test_vendor_label_content_has_correct_value():
    content = "FROM ubi9\nCMD [\"bash\"]\n"
    result = apply_sbom_vendor_label_fix(content)
    assert 'LABEL vendor="Red Hat, Inc."' in result


# ---------------------------------------------------------------------------
# apply_rpm_repo_id_fix
# ---------------------------------------------------------------------------

def test_rpm_repo_id_basic():
    content = "[ubi-9-baseos-rpms]\nbaseurl=...\n"
    assert apply_rpm_repo_id_fix(content) == "[ubi-9-for-$basearch-baseos-rpms]\nbaseurl=...\n"


def test_rpm_repo_id_already_arch_specific():
    content = "[ubi-9-for-$basearch-baseos-rpms]\nbaseurl=...\n"
    assert apply_rpm_repo_id_fix(content) == content


def test_rpm_repo_id_multiple_sections():
    content = "[ubi-9-baseos-rpms]\n[ubi-9-appstream-rpms]\n"
    result = apply_rpm_repo_id_fix(content)
    assert "[ubi-9-for-$basearch-baseos-rpms]" in result
    assert "[ubi-9-for-$basearch-appstream-rpms]" in result


def test_rpm_repo_id_mixed_fixed_and_unfixed():
    content = "[ubi-9-baseos-rpms]\n[ubi-9-for-$basearch-appstream-rpms]\n"
    result = apply_rpm_repo_id_fix(content)
    assert "[ubi-9-for-$basearch-baseos-rpms]" in result
    assert result.count("[ubi-9-for-$basearch-appstream-rpms]") == 1


def test_rpm_repo_id_ubi8():
    content = "[ubi-8-baseos-rpms]\n"
    assert apply_rpm_repo_id_fix(content) == "[ubi-8-for-$basearch-baseos-rpms]\n"


def test_rpm_repo_id_no_match():
    content = "# just a comment\nsome=value\n"
    assert apply_rpm_repo_id_fix(content) == content


# ---------------------------------------------------------------------------
# find_floating_bundle_refs
# ---------------------------------------------------------------------------

def test_floating_refs_empty():
    assert find_floating_bundle_refs("") == []


def test_floating_refs_no_quay_refs():
    assert find_floating_bundle_refs("bundle: docker.io/some/image:latest") == []


def test_floating_refs_pinned_is_excluded():
    content = "bundle: quay.io/konflux-ci/task-clone:0.1@sha256:abcdef1234567890\n"
    assert find_floating_bundle_refs(content) == []


def test_floating_refs_floating_found():
    content = "bundle: quay.io/konflux-ci/task-clone:0.1\n"
    refs = find_floating_bundle_refs(content)
    assert len(refs) == 1
    full_ref, repo_path, tag = refs[0]
    assert full_ref == "quay.io/konflux-ci/task-clone:0.1"
    assert repo_path == "konflux-ci/task-clone"
    assert tag == "0.1"


def test_floating_refs_deduplication():
    content = (
        "bundle: quay.io/konflux-ci/task-clone:0.1\n"
        "bundle: quay.io/konflux-ci/task-clone:0.1\n"
    )
    refs = find_floating_bundle_refs(content)
    assert len(refs) == 1


def test_floating_refs_mixed_pinned_and_floating():
    content = (
        "quay.io/org/task-a:0.3@sha256:abc123\n"
        "quay.io/org/task-b:0.2\n"
    )
    refs = find_floating_bundle_refs(content)
    assert len(refs) == 1
    assert refs[0][0] == "quay.io/org/task-b:0.2"


def test_floating_refs_with_oci_prefix():
    # oci:// prefix is before quay.io; regex matches the quay.io part
    content = "bundle: oci://quay.io/org/task-scan:0.3\n"
    refs = find_floating_bundle_refs(content)
    assert len(refs) == 1
    assert "quay.io/org/task-scan:0.3" in refs[0][0]


def test_floating_refs_multiple_distinct():
    content = "quay.io/org/task-a:0.1\nquay.io/org/task-b:0.2\n"
    refs = find_floating_bundle_refs(content)
    assert len(refs) == 2


# ---------------------------------------------------------------------------
# parse_deprecated_task_fixes
# ---------------------------------------------------------------------------

_OLD_REF = "oci://quay.io/org/task:0.1@sha256:aaa111"
_NEW_REF = "oci://quay.io/org/task:0.1@sha256:bbb222"

def _violation(rule, solution):
    return {'rule': rule, 'solution': solution}


def test_deprecated_fixes_none_input():
    assert parse_deprecated_task_fixes(None) == []


def test_deprecated_fixes_invalid_json():
    assert parse_deprecated_task_fixes("not-json{") == []


def test_deprecated_fixes_empty_dict():
    assert parse_deprecated_task_fixes({}) == []


def test_deprecated_fixes_wrong_rule():
    data = {'violations': [_violation('other_rule', f'use {_NEW_REF}')]}
    assert parse_deprecated_task_fixes(data) == []


def test_deprecated_fixes_single_ref_in_solution():
    # Only one oci:// ref in solution — need 2 to form a pair
    data = {'violations': [_violation('policy_deprecated_task', f'Update to {_NEW_REF}')]}
    assert parse_deprecated_task_fixes(data) == []


def test_deprecated_fixes_valid_pair():
    solution = f"Replace `{_OLD_REF}` with `{_NEW_REF}`"
    data = {'violations': [_violation('policy_deprecated_task', solution)]}
    fixes = parse_deprecated_task_fixes(data)
    assert len(fixes) == 1
    assert fixes[0]['old_ref'] == _OLD_REF
    assert fixes[0]['new_ref'] == _NEW_REF


def test_deprecated_fixes_identical_refs_skipped():
    solution = f"Replace `{_OLD_REF}` with `{_OLD_REF}`"
    data = {'violations': [_violation('policy_deprecated_task', solution)]}
    assert parse_deprecated_task_fixes(data) == []


def test_deprecated_fixes_nested_components_structure():
    solution = f"Replace `{_OLD_REF}` with `{_NEW_REF}`"
    data = {
        'components': [
            {'violations': [_violation('policy_deprecated_task', solution)]},
        ]
    }
    fixes = parse_deprecated_task_fixes(data)
    assert len(fixes) == 1


def test_deprecated_fixes_json_string_input():
    solution = f"Replace `{_OLD_REF}` with `{_NEW_REF}`"
    data = json.dumps({'violations': [_violation('policy_deprecated_task', solution)]})
    fixes = parse_deprecated_task_fixes(data)
    assert len(fixes) == 1


def test_deprecated_fixes_deduplication():
    solution = f"Replace `{_OLD_REF}` with `{_NEW_REF}`"
    data = {
        'violations': [
            _violation('policy_deprecated_task', solution),
            _violation('policy_deprecated_task', solution),
        ]
    }
    assert len(parse_deprecated_task_fixes(data)) == 1


# ---------------------------------------------------------------------------
# parse_untrusted_image_refs
# ---------------------------------------------------------------------------

_PINNED_REF = "quay.io/org/task:0.1@sha256:aaa111bbb222ccc333"
_OCI_PINNED_REF = "oci://quay.io/org/task:0.1@sha256:aaa111bbb222ccc333"


def test_untrusted_refs_none():
    assert parse_untrusted_image_refs(None) == []


def test_untrusted_refs_empty():
    assert parse_untrusted_image_refs({}) == []


def test_untrusted_refs_from_msg():
    data = {'violations': [{'msg': f"Bundle {_PINNED_REF} is too old", 'solution': ''}]}
    refs = parse_untrusted_image_refs(data)
    assert _PINNED_REF in refs


def test_untrusted_refs_from_solution():
    data = {'violations': [{'msg': '', 'solution': f"Update {_PINNED_REF}"}]}
    refs = parse_untrusted_image_refs(data)
    assert _PINNED_REF in refs


def test_untrusted_refs_oci_prefix():
    data = {'violations': [{'msg': f"Untrusted: {_OCI_PINNED_REF}", 'solution': ''}]}
    refs = parse_untrusted_image_refs(data)
    assert _OCI_PINNED_REF in refs


def test_untrusted_refs_deduplication():
    data = {
        'violations': [
            {'msg': _PINNED_REF, 'solution': _PINNED_REF, 'description': _PINNED_REF},
        ]
    }
    refs = parse_untrusted_image_refs(data)
    assert refs.count(_PINNED_REF) == 1


def test_untrusted_refs_multiple_violations():
    ref_a = "quay.io/org/task-a:0.1@sha256:aaa111"
    ref_b = "quay.io/org/task-b:0.2@sha256:bbb222"
    data = {
        'violations': [
            {'msg': ref_a, 'solution': ''},
            {'msg': ref_b, 'solution': ''},
        ]
    }
    refs = parse_untrusted_image_refs(data)
    assert ref_a in refs
    assert ref_b in refs


def test_untrusted_refs_no_digest_not_matched():
    # Refs without @sha256: should not be returned
    data = {'violations': [{'msg': 'quay.io/org/task:0.1 is old', 'solution': ''}]}
    assert parse_untrusted_image_refs(data) == []


# ---------------------------------------------------------------------------
# _refresh_pinned_ref
# ---------------------------------------------------------------------------

_OLD_PINNED = "quay.io/org/task:0.1@sha256:a1b2c3d4e5f60000"
_NEW_DIGEST = "sha256:b2c3d4e5f6a1bb11"


def test_refresh_malformed_ref():
    assert _refresh_pinned_ref("not-a-ref") is None


def test_refresh_resolution_fails():
    with patch('fixers.fix_generator.resolve_quay_digest', return_value=''):
        assert _refresh_pinned_ref(_OLD_PINNED) is None


def test_refresh_same_digest_returns_old_ref():
    with patch('fixers.fix_generator.resolve_quay_digest', return_value='sha256:a1b2c3d4e5f60000'):
        result = _refresh_pinned_ref(_OLD_PINNED)
        assert result == _OLD_PINNED


def test_refresh_new_digest_updates_ref():
    with patch('fixers.fix_generator.resolve_quay_digest', return_value=_NEW_DIGEST):
        result = _refresh_pinned_ref(_OLD_PINNED)
        assert result == f"quay.io/org/task:0.1@{_NEW_DIGEST}"


def test_refresh_oci_prefix_preserved():
    old_ref = "oci://quay.io/org/task:0.1@sha256:a1b2c3d4e5f60000"
    with patch('fixers.fix_generator.resolve_quay_digest', return_value=_NEW_DIGEST):
        result = _refresh_pinned_ref(old_ref)
        assert result.startswith("oci://")
        assert _NEW_DIGEST in result


def test_refresh_no_prefix_no_prefix_in_result():
    with patch('fixers.fix_generator.resolve_quay_digest', return_value=_NEW_DIGEST):
        result = _refresh_pinned_ref(_OLD_PINNED)
        assert not result.startswith("oci://")


# ---------------------------------------------------------------------------
# resolve_quay_digest
# ---------------------------------------------------------------------------

def _mock_response(digest=None):
    resp = MagicMock()
    resp.headers.get.side_effect = lambda key, default='': (digest if digest is not None else default) if key == 'Docker-Content-Digest' else default
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_resolve_returns_digest():
    mock_resp = _mock_response('sha256:abcdef1234')
    with patch('fixers.fix_generator.urllib.request.urlopen', return_value=mock_resp):
        result = resolve_quay_digest('org/task', '0.1')
    assert result == 'sha256:abcdef1234'


def test_resolve_missing_header_returns_empty():
    mock_resp = _mock_response(None)
    with patch('fixers.fix_generator.urllib.request.urlopen', return_value=mock_resp):
        result = resolve_quay_digest('org/task', '0.1')
    assert result == ''


def test_resolve_exception_returns_empty():
    with patch('fixers.fix_generator.urllib.request.urlopen', side_effect=OSError("timeout")):
        result = resolve_quay_digest('org/task', '0.1')
    assert result == ''


def test_resolve_constructs_correct_url():
    mock_resp = _mock_response('sha256:abc')
    with patch('fixers.fix_generator.urllib.request.urlopen', return_value=mock_resp) as mock_open:
        resolve_quay_digest('konflux-ci/tekton-catalog/task-clone', '0.1')
    url_arg = mock_open.call_args[0][0]
    assert url_arg.full_url == 'https://quay.io/v2/konflux-ci/tekton-catalog/task-clone/manifests/0.1'
