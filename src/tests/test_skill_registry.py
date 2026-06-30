"""Tests for the skill registry: models, loader, registry, known sources."""

import os
import tempfile
import textwrap
import unittest

from skills.models import IcMetadata, SkillEntry, SkillMetadata, SourceEntry
from skills.loader import _parse_yaml_simple, discover_skills, parse_skill_md
from skills.registry import SkillRegistry
from skills.known_sources import KNOWN_SOURCES, resolve_source


class TestSkillMetadata(unittest.TestCase):

    def test_from_dict_basic(self):
        meta = SkillMetadata.from_dict({'name': 'test', 'description': 'A test skill'})
        self.assertEqual(meta.name, 'test')
        self.assertEqual(meta.description, 'A test skill')
        self.assertEqual(meta.allowed_tools, 'Bash')
        self.assertFalse(meta.user_invocable)

    def test_from_dict_kebab_case(self):
        meta = SkillMetadata.from_dict({
            'name': 'test',
            'description': 'desc',
            'allowed-tools': 'Bash, Read',
            'user-invocable': True,
        })
        self.assertEqual(meta.allowed_tools, 'Bash, Read')
        self.assertTrue(meta.user_invocable)

    def test_roundtrip(self):
        original = SkillMetadata(
            name='x', description='y', allowed_tools='Bash',
            user_invocable=True, category='onboarding',
            ic_metadata=IcMetadata(requires_tools=['oc', 'git']),
        )
        d = original.to_dict()
        restored = SkillMetadata.from_dict(d)
        self.assertEqual(restored.name, 'x')
        self.assertTrue(restored.user_invocable)
        self.assertEqual(restored.ic_metadata.requires_tools, ['oc', 'git'])


class TestSkillEntry(unittest.TestCase):

    def test_qualified_name(self):
        entry = SkillEntry(
            name='fix-hermetic', source='aiops-infra', path='/tmp/x',
            status='active', metadata=SkillMetadata(name='fix-hermetic', description=''),
        )
        self.assertEqual(entry.qualified_name, 'aiops-infra/fix-hermetic')

    def test_tags_roundtrip(self):
        entry = SkillEntry(
            name='x', source='src', path='/tmp', status='active',
            metadata=SkillMetadata(name='x', description=''),
            tags=['rhoai', 'onboarding'],
        )
        d = entry.to_dict()
        restored = SkillEntry.from_dict(d)
        self.assertEqual(restored.tags, ['rhoai', 'onboarding'])


class TestSourceEntry(unittest.TestCase):

    def test_roundtrip(self):
        src = SourceEntry(name='repo', url='https://example.com/repo',
                          commit='abc123', added_at='2026-01-01', local_path='/tmp/repo')
        restored = SourceEntry.from_dict(src.to_dict())
        self.assertEqual(restored.name, 'repo')
        self.assertEqual(restored.url, 'https://example.com/repo')


class TestYamlParser(unittest.TestCase):

    def test_simple_values(self):
        text = "name: my-skill\ndescription: A test\nuser-invocable: true"
        result = _parse_yaml_simple(text)
        self.assertEqual(result['name'], 'my-skill')
        self.assertTrue(result['user-invocable'])

    def test_inline_list(self):
        text = "requires-tools: [oc, git, jq]"
        result = _parse_yaml_simple(text)
        self.assertEqual(result['requires-tools'], ['oc', 'git', 'jq'])

    def test_multiline_list(self):
        text = "requires-tools:\n  - oc\n  - git"
        result = _parse_yaml_simple(text)
        self.assertEqual(result['requires-tools'], ['oc', 'git'])

    def test_quoted_value_with_colon(self):
        text = 'description: "Fix RHOAI: hermetic issues"'
        result = _parse_yaml_simple(text)
        self.assertEqual(result['description'], 'Fix RHOAI: hermetic issues')

    def test_false_value(self):
        text = "enabled: false"
        result = _parse_yaml_simple(text)
        self.assertFalse(result['enabled'])


class TestParseSkillMd(unittest.TestCase):

    def test_valid_skill_md(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(textwrap.dedent("""\
                ---
                name: create-quay-repo
                description: Creates a Quay repository
                allowed-tools: Bash
                user-invocable: true
                ---

                # Instructions
                Do the thing.
            """))
            f.flush()
            meta = parse_skill_md(f.name)
        os.unlink(f.name)
        self.assertEqual(meta.name, 'create-quay-repo')
        self.assertEqual(meta.description, 'Creates a Quay repository')
        self.assertTrue(meta.user_invocable)

    def test_missing_name(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("---\ndescription: no name\n---\nBody")
            f.flush()
            meta = parse_skill_md(f.name)
        os.unlink(f.name)
        self.assertIsNone(meta)

    def test_no_frontmatter(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Just a README\nNo frontmatter here.")
            f.flush()
            meta = parse_skill_md(f.name)
        os.unlink(f.name)
        self.assertIsNone(meta)


class TestDiscoverSkills(unittest.TestCase):

    def test_discovers_skills_in_claude_dir(self):
        with tempfile.TemporaryDirectory() as root:
            skill_dir = os.path.join(root, '.claude', 'skills', 'my-skill')
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, 'SKILL.md'), 'w') as f:
                f.write("---\nname: my-skill\ndescription: Test\nallowed-tools: Bash\n---\nBody")
            found = discover_skills(root)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0][1].name, 'my-skill')

    def test_discovers_skills_in_helpers_dir(self):
        with tempfile.TemporaryDirectory() as root:
            skill_dir = os.path.join(root, 'helpers', 'skills', 'util')
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, 'SKILL.md'), 'w') as f:
                f.write("---\nname: util\ndescription: Utility\nallowed-tools: Bash\n---\nBody")
            found = discover_skills(root)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0][1].name, 'util')

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as root:
            found = discover_skills(root)
            self.assertEqual(len(found), 0)


class TestSkillRegistry(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry_path = os.path.join(self.tmpdir, 'skills.json')
        self.registry = SkillRegistry(path=self.registry_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_and_list_skills(self):
        meta = SkillMetadata(name='test', description='A test', category='ci')
        self.registry.add_skill('test', 'repo', '/tmp/test', meta)
        skills = self.registry.list_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, 'test')
        self.assertIn('repo', skills[0].tags)
        self.assertIn('ci', skills[0].tags)

    def test_add_source_and_remove(self):
        self.registry.add_source('myrepo', 'https://example.com', 'abc', '/tmp/r')
        meta = SkillMetadata(name='s1', description='')
        self.registry.add_skill('s1', 'myrepo', '/tmp/s1', meta)
        self.registry.add_skill('s2', 'myrepo', '/tmp/s2',
                                SkillMetadata(name='s2', description=''))
        count = self.registry.remove_source('myrepo')
        self.assertEqual(count, 2)
        self.assertEqual(len(self.registry.list_skills()), 0)

    def test_tag_add_remove(self):
        meta = SkillMetadata(name='x', description='')
        self.registry.add_skill('x', 'src', '/tmp/x', meta)
        self.registry.add_tag('x', 'rhoai')
        skill = self.registry.get_skill('x')
        self.assertIn('rhoai', skill.tags)
        self.registry.remove_tag('x', 'rhoai')
        self.assertNotIn('rhoai', skill.tags)

    def test_filter_by_tag(self):
        self.registry.add_skill('a', 'src', '/tmp/a',
                                SkillMetadata(name='a', description=''))
        self.registry.add_skill('b', 'src', '/tmp/b',
                                SkillMetadata(name='b', description=''))
        self.registry.add_tag('a', 'onboarding')
        result = self.registry.list_skills(tag='onboarding')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'a')

    def test_save_and_reload(self):
        meta = SkillMetadata(name='persist', description='Persists')
        self.registry.add_source('repo', 'https://example.com', 'abc', '/tmp')
        self.registry.add_skill('persist', 'repo', '/tmp/p', meta, initial_tags=['custom'])
        self.registry.save()
        reloaded = SkillRegistry(path=self.registry_path)
        skills = reloaded.list_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, 'persist')
        self.assertIn('custom', skills[0].tags)
        self.assertIn('repo', skills[0].tags)

    def test_name_collision_qualified(self):
        self.registry.add_skill('fix', 'repo-a', '/tmp/a',
                                SkillMetadata(name='fix', description=''))
        self.registry.add_skill('fix', 'repo-b', '/tmp/b',
                                SkillMetadata(name='fix', description=''))
        with self.assertRaises(KeyError) as ctx:
            self.registry.get_skill('fix')
        self.assertIn('Ambiguous', str(ctx.exception))
        self.assertIsNotNone(self.registry.get_skill('repo-a/fix'))
        self.assertIsNotNone(self.registry.get_skill('repo-b/fix'))

    def test_list_tags(self):
        self.registry.add_skill('a', 'src', '/tmp/a',
                                SkillMetadata(name='a', description=''))
        self.registry.add_skill('b', 'src', '/tmp/b',
                                SkillMetadata(name='b', description=''))
        self.registry.add_tag('a', 'rhoai')
        self.registry.add_tag('b', 'rhoai')
        self.registry.add_tag('a', 'onboarding')
        tags = self.registry.list_tags()
        self.assertEqual(tags['src'], 2)
        self.assertEqual(tags['rhoai'], 2)
        self.assertEqual(tags['onboarding'], 1)


class TestKnownSources(unittest.TestCase):

    def test_resolve_known(self):
        name, url, branch = resolve_source('aiops-infra')
        self.assertEqual(name, 'aiops-infra')
        self.assertIn('github.com', url)

    def test_resolve_url(self):
        name, url, branch = resolve_source('https://github.com/org/my-repo')
        self.assertEqual(name, 'my-repo')
        self.assertEqual(url, 'https://github.com/org/my-repo')

    def test_resolve_unknown(self):
        with self.assertRaises(ValueError):
            resolve_source('nonexistent-shorthand')

    def test_known_sources_catalog(self):
        self.assertIn('aiops-infra', KNOWN_SOURCES)
        self.assertIn('ai-helpers', KNOWN_SOURCES)
        for name, info in KNOWN_SOURCES.items():
            self.assertIn('url', info)
            self.assertIn('description', info)


if __name__ == '__main__':
    unittest.main()
