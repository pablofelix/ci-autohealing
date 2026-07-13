"""Sandboxed command execution for agent-mode skills.

Provides an abstract ToolSandbox interface with a LocalSandbox
implementation (subprocess). Future K8sSandbox will implement the
same interface for cluster-based execution.
"""

import logging
import os
import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass

from security.redact import get_safe_env, sanitize_for_llm

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SandboxResult:
    """Result of a single sandboxed command execution."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class ToolSandbox(ABC):
    """Abstract interface for sandboxed command execution."""

    @abstractmethod
    def execute(self, command, timeout=60, language='bash'):
        """Execute a command and return SandboxResult."""

    @abstractmethod
    def read_file(self, path):
        """Read a file from the sandbox working directory."""

    @abstractmethod
    def write_file(self, path, content):
        """Write a file in the sandbox working directory."""

    @abstractmethod
    def cleanup(self):
        """Release sandbox resources."""


class LocalSandbox(ToolSandbox):
    """Subprocess-based sandbox with env filtering and output limits."""

    def __init__(self, working_dir=None, declared_env_vars=None,
                 max_output_bytes=50000):
        self._working_dir = working_dir or os.getcwd()
        self._env = get_safe_env(declared_env_vars)
        self._max_output = max_output_bytes
        self._temp_dir = None

    # Word-boundary regex patterns resist quoting/variable-expansion bypass
    DENIED_PATTERNS = (
        (r'\brm\s+-\w*r\w*f\s+[/~]', 'recursive delete of / or ~'),
        (r'\bmkfs\b', 'filesystem format'),
        (r'\bdd\s+if=', 'raw disk write'),
        (r'\bcurl\b', 'network request (curl)'),
        (r'\bwget\b', 'network request (wget)'),
        (r'\bnc(at)?\b', 'network connect (nc/ncat)'),
        (r'>\s*/etc/', 'write to /etc'),
        (r'>\s*/dev/', 'write to /dev'),
        (r'\bchmod\s+777\b', 'world-writable permissions'),
        (r'\beval\s*\$\(', 'eval command substitution'),
        (r'\|\s*(ba)?sh\b', 'pipe to shell'),
    )
    _COMPILED_DENIED = [(re.compile(p, re.IGNORECASE), desc) for p, desc in DENIED_PATTERNS]

    # Shell expansion tricks that indicate bypass attempts
    _EVASION_PATTERNS = re.compile(
        r"(?:"
        r"['\"]'['\"]"          # quote-splitting: cu''rl
        r"|\\."                 # backslash escaping: cur\l
        r"|\$\([^)]+\)"        # command substitution: $(echo curl)
        r"|\$\{[^}]+\}"        # variable expansion: ${cmd}
        r"|\bbase64\b"         # base64 encoding
        r")",
        re.IGNORECASE,
    )

    def execute(self, command, timeout=60, language='bash'):
        cmd_lower = command.lower().strip()
        for pattern, desc in self._COMPILED_DENIED:
            if pattern.search(cmd_lower):
                logger.warning("Sandbox denied command (%s): %.120s", desc, command)
                return SandboxResult(
                    exit_code=-1, stdout='',
                    stderr='Command denied: {}'.format(desc),
                )

        if self._EVASION_PATTERNS.search(command):
            logger.warning("Sandbox denied command (evasion pattern): %.120s", command)
            return SandboxResult(
                exit_code=-1, stdout='',
                stderr='Command denied: contains shell evasion patterns',
            )

        if language == 'python':
            cmd = ['python3', '-c', command]
        else:
            cmd = ['bash', '-c', command]

        logger.info("Sandbox exec [%s]: %.120s", language, command)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._env,
                cwd=self._working_dir,
            )
            stdout = self._truncate(proc.stdout)
            stderr = self._truncate(proc.stderr)

            return SandboxResult(
                exit_code=proc.returncode,
                stdout=sanitize_for_llm(stdout),
                stderr=sanitize_for_llm(stderr),
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                exit_code=-1,
                stdout='',
                stderr='Command timed out after {} seconds'.format(timeout),
                timed_out=True,
            )
        except Exception as e:
            return SandboxResult(
                exit_code=-1,
                stdout='',
                stderr='Sandbox error: {}'.format(e),
            )

    def read_file(self, path):
        resolved = self._resolve_path(path)
        try:
            with open(resolved) as f:
                content = f.read()
            return SandboxResult(
                exit_code=0,
                stdout=self._truncate(content),
                stderr='',
            )
        except Exception as e:
            return SandboxResult(
                exit_code=1,
                stdout='',
                stderr='Failed to read {}: {}'.format(path, e),
            )

    def write_file(self, path, content):
        resolved = self._resolve_path(path)
        try:
            parent = os.path.dirname(resolved)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            with open(resolved, 'w') as f:
                f.write(content)
            return SandboxResult(
                exit_code=0,
                stdout='Wrote {} bytes to {}'.format(len(content), path),
                stderr='',
            )
        except Exception as e:
            return SandboxResult(
                exit_code=1,
                stdout='',
                stderr='Failed to write {}: {}'.format(path, e),
            )

    def cleanup(self):
        pass

    def _truncate(self, text):
        if not text:
            return ''
        if len(text) > self._max_output:
            return text[:self._max_output] + '\n... [truncated at {} bytes]'.format(
                self._max_output)
        return text

    def _resolve_path(self, path):
        if os.path.isabs(path):
            resolved = os.path.realpath(path)
        else:
            resolved = os.path.realpath(os.path.join(self._working_dir, path))
        working_real = os.path.realpath(self._working_dir)
        if not resolved.startswith(working_real + os.sep) and resolved != working_real:
            raise ValueError("Path escapes sandbox: {}".format(path))
        return resolved
