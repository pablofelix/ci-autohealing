"""Skill execution engine — runs skills with sandboxing based on risk level."""

import logging
import os
import subprocess
import time
from datetime import UTC, datetime

from skills.models import ExecutionResult
from skills.validator import SkillValidator, assess_risk, check_prerequisites, classify_risk

logger = logging.getLogger(__name__)


def _extract_code_blocks(skill_md_path):
    """Extract executable code blocks from SKILL.md."""
    blocks = []
    try:
        with open(skill_md_path) as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return blocks

    in_frontmatter = False
    in_block = False
    in_skip = False
    lang = ''
    current = []

    for line in content.split('\n'):
        if line.strip() == '---' and not in_block and not in_skip:
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue

        if line.startswith('```') and in_skip:
            in_skip = False
            continue

        if line.startswith('```') and not in_block:
            lang = line[3:].strip().split()[0] if line[3:].strip() else ''
            if lang in ('bash', 'sh', 'shell', 'python', 'python3'):
                in_block = True
                current = []
            else:
                in_skip = True
            continue

        if line.startswith('```') and in_block:
            in_block = False
            code = '\n'.join(current).strip()
            if code and not code.startswith('# ic:skip') and not code.startswith('# example'):
                blocks.append({'lang': lang, 'code': code})
            continue

        if in_block:
            current.append(line)

    return blocks


class SkillExecutor:
    """Execute skills with risk-based sandboxing."""

    def __init__(self, skill, params=None, dry_run=False, timeout=300,
                 env_overrides=None, triggered_by='cli',
                 component_name=None, application=None, triage_item_id=None):
        self.skill = skill
        self.params = params or {}
        self.dry_run = dry_run
        self.timeout = timeout
        self.env_overrides = env_overrides or {}
        self.triggered_by = triggered_by
        self.component_name = component_name
        self.application = application
        self.triage_item_id = triage_item_id

    def _skill_dir(self):
        from skills.models import resolve_working_dir
        return resolve_working_dir(self.skill.path, self.skill.metadata.working_dir)

    def _skill_md_path(self):
        # SKILL.md lives in the skill's own directory, not the resolved working_dir
        skill_md = os.path.join(self.skill.path, 'SKILL.md')
        if os.path.isfile(skill_md):
            return skill_md
        return None

    def check_prerequisites(self):
        return check_prerequisites(self.skill.metadata)

    def classify(self):
        validator = SkillValidator()
        result = validator.validate(self._skill_dir(), self.skill.metadata)
        return classify_risk(self.skill.metadata, result)

    def assess(self):
        validator = SkillValidator()
        result = validator.validate(self._skill_dir(), self.skill.metadata)
        return assess_risk(self.skill.metadata, result)

    def _result(self, **kwargs):
        kwargs.setdefault('triggered_by', self.triggered_by)
        kwargs.setdefault('component_name', self.component_name)
        kwargs.setdefault('application', self.application)
        kwargs.setdefault('triage_item_id', self.triage_item_id)
        return ExecutionResult(**kwargs)

    def execute(self):
        started = datetime.now(UTC).isoformat()
        risk = self.classify()

        prereqs = self.check_prerequisites()
        if prereqs['status'] == 'fail':
            missing_tools = [t for t, ok in prereqs.get('tools', {}).items() if not ok]
            return self._result(
                skill_name=self.skill.qualified_name,
                status='prereq_failed',
                stderr='Missing tools: {}'.format(', '.join(missing_tools)),
                risk_level=risk,
                started_at=started,
            )

        skill_md = self._skill_md_path()
        if not skill_md:
            return self._result(
                skill_name=self.skill.qualified_name,
                status='failed',
                stderr='No SKILL.md found',
                risk_level=risk,
                started_at=started,
            )

        blocks = _extract_code_blocks(skill_md)
        if not blocks:
            return self._result(
                skill_name=self.skill.qualified_name,
                status='failed',
                stderr='No executable code blocks found in SKILL.md',
                risk_level=risk,
                started_at=started,
            )

        if risk == 'high' and not self.dry_run:
            from skills.sandbox import ContainerSandbox
            sandbox = ContainerSandbox(timeout=self.timeout)
            return sandbox.run(self.skill, blocks, params=self.params,
                               triggered_by=self.triggered_by)

        if self.dry_run:
            return self._result(
                skill_name=self.skill.qualified_name,
                status='dry_run',
                risk_level=risk,
                started_at=started,
                steps_total=len(blocks),
                dry_run_steps=[b['code'] for b in blocks],
            )

        from security.redact import get_safe_env
        declared_env = []
        if self.skill.metadata.ic_metadata:
            declared_env = self.skill.metadata.ic_metadata.requires_env
        env = get_safe_env(declared_vars=declared_env)

        allowed_keys = set(declared_env) | set(env.keys())
        for k, v in self.env_overrides.items():
            if k in allowed_keys:
                env[k] = str(v)
            else:
                logger.warning("env_overrides key %s not in declared_env, skipped", k)
        for k, v in self.params.items():
            if k in allowed_keys:
                env[k] = str(v)
            else:
                logger.warning("param key %s not in declared_env, skipped", k)

        all_stdout = []
        all_stderr = []
        steps_done = 0
        t0 = time.time()

        for i, block in enumerate(blocks):
            logger.info("[%s] Step %d/%d (%s)", self.skill.name, i + 1, len(blocks), block['lang'])

            if block['lang'] in ('python', 'python3'):
                cmd = ['python3', '-c', block['code']]
            else:
                cmd = ['bash', '-c', block['code']]

            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=self.timeout, env=env,
                    cwd=self._skill_dir(),
                )
                all_stdout.append(proc.stdout)
                all_stderr.append(proc.stderr)
                steps_done += 1

                if proc.returncode != 0:
                    return self._result(
                        skill_name=self.skill.qualified_name,
                        status='failed',
                        exit_code=proc.returncode,
                        stdout='\n'.join(all_stdout),
                        stderr='\n'.join(all_stderr),
                        duration_seconds=time.time() - t0,
                        risk_level=risk,
                        started_at=started,
                        steps_executed=steps_done,
                        steps_total=len(blocks),
                    )

            except subprocess.TimeoutExpired:
                all_stderr.append('Step {} timed out after {}s'.format(i + 1, self.timeout))
                return self._result(
                    skill_name=self.skill.qualified_name,
                    status='failed',
                    exit_code=-1,
                    stdout='\n'.join(all_stdout),
                    stderr='\n'.join(all_stderr),
                    duration_seconds=time.time() - t0,
                    risk_level=risk,
                    started_at=started,
                    steps_executed=steps_done,
                    steps_total=len(blocks),
                )

        return self._result(
            skill_name=self.skill.qualified_name,
            status='success',
            exit_code=0,
            stdout='\n'.join(all_stdout),
            stderr='\n'.join(all_stderr),
            duration_seconds=time.time() - t0,
            risk_level=risk,
            started_at=started,
            steps_executed=steps_done,
            steps_total=len(blocks),
        )
