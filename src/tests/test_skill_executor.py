"""Tests for skill executor and risk classification."""

import os
import tempfile

from skills.models import ExecutionResult, IcMetadata, SkillMetadata
from skills.validator import RiskAssessment, ValidationResult, assess_risk, classify_risk


def _make_metadata(tools=None, env=None, **kwargs):
    ic = IcMetadata(requires_tools=tools or [], requires_env=env or [])
    return SkillMetadata(name='test', description='test skill',
                         ic_metadata=ic, **kwargs)


class TestRiskClassification:
    def test_read_only_tools_are_low(self):
        m = _make_metadata(tools=['jq', 'grep'])
        assert classify_risk(m) == 'low'

    def test_write_tools_are_medium(self):
        m = _make_metadata(tools=['git', 'gh'])
        assert classify_risk(m) == 'medium'

    def test_secret_env_is_medium(self):
        m = _make_metadata(env=['GITHUB_TOKEN'])
        assert classify_risk(m) == 'medium'

    def test_destructive_tools_are_high(self):
        m = _make_metadata(tools=['rm'])
        assert classify_risk(m) == 'high'

    def test_critical_findings_are_high(self):
        m = _make_metadata()
        vr = ValidationResult(skill_name='test')
        from skills.validator import Finding
        vr.findings.append(Finding('critical', 'secret', 'found key', 'f.sh', 1))
        assert classify_risk(m, vr) == 'high'

    def test_no_tools_defaults_medium(self):
        m = _make_metadata()
        assert classify_risk(m) == 'medium'

    def test_assess_returns_reasons(self):
        m = _make_metadata(tools=['oc', 'git'], env=['GITHUB_TOKEN'])
        a = assess_risk(m)
        assert isinstance(a, RiskAssessment)
        assert a.level == 'medium'
        assert any('write tools' in r for r in a.reasons)
        assert any('GITHUB_TOKEN' in r for r in a.reasons)


class TestCodeBlockExtraction:
    def test_extracts_bash_blocks(self):
        from skills.executor import _extract_code_blocks
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write('---\nname: test\n---\n\n# Steps\n\n```bash\necho hello\n```\n')
            f.flush()
            blocks = _extract_code_blocks(f.name)
        os.unlink(f.name)
        assert len(blocks) == 1
        assert blocks[0]['code'] == 'echo hello'
        assert blocks[0]['lang'] == 'bash'

    def test_skips_ic_skip_blocks(self):
        from skills.executor import _extract_code_blocks
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write('```bash\n# ic:skip\necho do not run\n```\n\n```bash\necho run this\n```\n')
            f.flush()
            blocks = _extract_code_blocks(f.name)
        os.unlink(f.name)
        assert len(blocks) == 1
        assert 'run this' in blocks[0]['code']

    def test_skips_example_blocks(self):
        from skills.executor import _extract_code_blocks
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write('```bash\n# example\necho sample output\n```\n\n```bash\necho real\n```\n')
            f.flush()
            blocks = _extract_code_blocks(f.name)
        os.unlink(f.name)
        assert len(blocks) == 1
        assert 'real' in blocks[0]['code']

    def test_skips_non_executable_langs(self):
        from skills.executor import _extract_code_blocks
        content = (
            "Some text\n"
            "\x60\x60\x60yaml\n"
            "key: value\n"
            "\x60\x60\x60\n"
            "\n"
            "\x60\x60\x60bash\n"
            "echo yes\n"
            "\x60\x60\x60\n"
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(content)
            f.flush()
            blocks = _extract_code_blocks(f.name)
        os.unlink(f.name)
        assert len(blocks) == 1
        assert blocks[0]['lang'] == 'bash'


class TestExecutionResult:
    def test_to_dict(self):
        r = ExecutionResult(skill_name='test', status='success', triggered_by='mcp')
        d = r.to_dict()
        assert d['triggered_by'] == 'mcp'
        assert d['status'] == 'success'
