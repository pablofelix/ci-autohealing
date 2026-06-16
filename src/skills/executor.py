"""Skill execution engine — runs skills with sandboxing based on risk level."""

import logging
import os
import subprocess
import time
from datetime import datetime, timezone

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
    lang = ''
    current = []

    for line in content.split('\n'):
        if line.strip() == '---' and not in_block:
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue

        if line.startswith('```') and not in_block:
            lang = line[3:].strip().split()[0] if line[3:].strip() else ''
            if lang in ('bash', 'sh', 'shell', 'python', 'python3', ''):
                in_block = True
                current = []
            continue

        if line.startswith('```') and in_block:
            in_block = False
            code = '\n'.join(current).strip()
            if code and not code.startswith('#'):
                blocks.append({'lang': lang or 'bash', 'code': code})
            continue

        if in_block:
            current.append(line)

    return blocks


class SkillExecutor:
    """Execute skills with risk-based sandboxing."""

    def __init__(self, skill, params=None, dry_run=False, timeout=300, env_overrides=None):
        self.skill = skill
        self.params = params or {}
        self.dry_run = dry_run
        self.timeout = timeout
        self.env_overrides = env_overrides or {}

    def _skill_dir(self):
        return self.skill.path

    def _skill_md_path(self):
        skill_md = os.path.join(self._skill_dir(), 'SKILL.md')
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

    def execute(self):
        started = datetime.now(timezone.utc).isoformat()
        risk = self.classify()

        prereqs = self.check_prerequisites()
        if prereqs['status'] == 'fail':
            missing_tools = [t for t, ok in prereqs.get('tools', {}).items() if not ok]
            return ExecutionResult(
                skill_name=self.skill.qualified_name,
                status='prereq_failed',
                stderr='Missing tools: {}'.format(', '.join(missing_tools)),
                risk_level=risk,
                started_at=started,
            )

        skill_md = self._skill_md_path()
        if not skill_md:
            return ExecutionResult(
                skill_name=self.skill.qualified_name,
                status='failed',
                stderr='No SKILL.md found',
                risk_level=risk,
                started_at=started,
            )

        blocks = _extract_code_blocks(skill_md)
        if not blocks:
            return ExecutionResult(
                skill_name=self.skill.qualified_name,
                status='failed',
                stderr='No executable code blocks found in SKILL.md',
                risk_level=risk,
                started_at=started,
            )

        if self.dry_run:
            return ExecutionResult(
                skill_name=self.skill.qualified_name,
                status='dry_run',
                risk_level=risk,
                started_at=started,
                steps_total=len(blocks),
                dry_run_steps=[b['code'] for b in blocks],
            )

        env = os.environ.copy()
        env.update(self.env_overrides)
        for k, v in self.params.items():
            env[k] = str(v)

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
                    return ExecutionResult(
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
                return ExecutionResult(
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

        return ExecutionResult(
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
