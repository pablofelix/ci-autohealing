"""Comprehensive tests for cli/log_filter.py -- maximize coverage."""

import pytest

from cli.log_filter import (
    _ERROR_PATTERN,
    CONTEXT_LINES,
    extract_failed_step_section,
    filter_error_context,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lines(n, prefix="line"):
    """Return a newline-joined string with *n* numbered lines."""
    return "\n".join(f"{prefix} {i}" for i in range(n))


def _make_taskrun_logs(*sections):
    """Build multi-section TaskRun logs.

    Each section is (name, line_count) -- generates numbered filler lines
    under an ``===== TaskRun: <name>`` header.
    """
    parts = []
    for name, count in sections:
        parts.append(f"===== TaskRun: {name}")
        for i in range(count):
            parts.append(f"  {name} output {i}")
    return "\n".join(parts)


# ===================================================================
# extract_failed_step_section
# ===================================================================

class TestExtractFailedStepSection:
    """Tests for extract_failed_step_section."""

    def test_none_failed_step_returns_full_logs(self):
        logs = "some\nlogs\nhere"
        assert extract_failed_step_section(logs, None) == logs

    def test_empty_string_failed_step_returns_full_logs(self):
        logs = "some\nlogs\nhere"
        assert extract_failed_step_section(logs, "") == logs

    def test_extracts_matching_section_over_10_lines(self):
        logs = _make_taskrun_logs(("build", 5), ("test", 15), ("deploy", 5))
        result = extract_failed_step_section(logs, "test")
        assert "===== TaskRun: test" in result
        assert "test output 0" in result
        assert "test output 14" in result
        # Other sections excluded
        assert "build output" not in result
        assert "deploy output" not in result

    def test_matching_section_lte_10_lines_returns_full_logs(self):
        """Section with <= 10 lines triggers fallback to full logs."""
        # Header + 8 content lines = 9 lines total (<=10)
        logs = _make_taskrun_logs(("build", 5), ("test", 8), ("deploy", 5))
        result = extract_failed_step_section(logs, "test")
        # Falls back to full logs because extracted section <= 10 lines
        assert "build output" in result
        assert "deploy output" in result

    def test_no_matching_section_returns_full_logs(self):
        logs = _make_taskrun_logs(("build", 20), ("deploy", 20))
        result = extract_failed_step_section(logs, "nonexistent-step")
        assert result == logs

    def test_target_in_middle_of_multiple_sections(self):
        logs = _make_taskrun_logs(
            ("init", 12), ("compile", 15), ("test", 12), ("package", 12)
        )
        result = extract_failed_step_section(logs, "compile")
        assert "===== TaskRun: compile" in result
        assert "compile output 0" in result
        assert "compile output 14" in result
        assert "init output" not in result
        assert "test output" not in result
        assert "package output" not in result

    def test_section_ends_at_next_taskrun_header(self):
        """Verify that capture stops when a new TaskRun header is seen."""
        logs = _make_taskrun_logs(("alpha", 20), ("beta", 20))
        result = extract_failed_step_section(logs, "alpha")
        assert "alpha output 19" in result
        assert "beta output" not in result

    def test_last_section_captured_to_end(self):
        """Last section has no following header -- captured to EOF."""
        logs = _make_taskrun_logs(("first", 5), ("last", 20))
        result = extract_failed_step_section(logs, "last")
        assert "last output 19" in result
        assert "first output" not in result


# ===================================================================
# filter_error_context
# ===================================================================

class TestFilterErrorContextBasic:
    """Basic / edge-case tests for filter_error_context."""

    def test_none_logs(self):
        text, stats = filter_error_context(None)
        assert text == ""
        assert stats == {"total_lines": 0, "match_count": 0, "filtered": False}

    def test_empty_string_logs(self):
        text, stats = filter_error_context("")
        assert text == ""
        assert stats["filtered"] is False

    def test_short_logs_returned_unfiltered(self):
        """Logs with <= 80 lines are returned as-is."""
        logs = _make_lines(80)
        text, stats = filter_error_context(logs)
        assert text == logs
        assert stats["total_lines"] == 80
        assert stats["match_count"] == 0
        assert stats["filtered"] is False

    def test_exactly_80_lines_not_filtered(self):
        logs = _make_lines(80)
        _, stats = filter_error_context(logs)
        assert stats["filtered"] is False

    def test_81_lines_triggers_filtering(self):
        """81 lines crosses the threshold -- filtering kicks in."""
        lines = [f"clean line {i}" for i in range(80)]
        lines.append("ERROR something broke")
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs)
        assert stats["filtered"] is True
        assert stats["total_lines"] == 81


class TestFilterErrorContextPatterns:
    """Tests for log filtering with error patterns present."""

    def test_long_logs_with_error_returns_context(self):
        lines = [f"ok {i}" for i in range(100)]
        lines[50] = "[2024-01-01] Error: build failed"
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs)
        assert stats["filtered"] is True
        assert stats["match_count"] >= 1
        assert "[2024-01-01] Error: build failed" in text

    def test_no_error_patterns_returns_last_30_lines(self):
        lines = [f"clean {i}" for i in range(200)]
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs)
        assert stats["filtered"] is True
        assert stats["match_count"] == 0
        assert "note" in stats
        assert "last 30 lines" in stats["note"]
        # Verify it's actually the last 30
        result_lines = text.split("\n")
        assert len(result_lines) == 30
        assert result_lines[-1] == "clean 199"
        assert result_lines[0] == "clean 170"


class TestFilterErrorContextMerging:
    """Tests for context window merging behaviour."""

    def test_close_errors_merge_ranges(self):
        """Two errors within 2*context+1 lines produce a single merged range."""
        lines = [f"filler {i}" for i in range(100)]
        # Errors at 40 and 45 -- with default context=5, ranges overlap
        lines[40] = "ERROR first"
        lines[45] = "ERROR second"
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs)
        assert stats["match_count"] == 2
        # No separator because ranges merged
        assert "  ..." not in text
        assert "ERROR first" in text
        assert "ERROR second" in text

    def test_far_apart_errors_produce_separator(self):
        """Two errors far apart produce separate ranges with '  ...' separator."""
        lines = [f"filler {i}" for i in range(100)]
        lines[10] = "ERROR early"
        lines[90] = "ERROR late"
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs)
        assert stats["match_count"] == 2
        assert "  ..." in text
        assert "ERROR early" in text
        assert "ERROR late" in text

    def test_error_at_start_of_log(self):
        """Error on line 0 -- context start clamped to 0."""
        lines = [f"filler {i}" for i in range(100)]
        lines[0] = "ERROR at start"
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs)
        assert "ERROR at start" in text
        result_lines = text.split("\n")
        assert result_lines[0] == "ERROR at start"

    def test_error_at_end_of_log(self):
        """Error on last line -- context end clamped to total-1."""
        lines = [f"filler {i}" for i in range(100)]
        lines[99] = "ERROR at end"
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs)
        assert "ERROR at end" in text
        result_lines = text.split("\n")
        assert result_lines[-1] == "ERROR at end"

    def test_context_window_size(self):
        """Verify the right number of context lines surround an error."""
        lines = [f"ctx {i}" for i in range(100)]
        lines[50] = "FATAL crash"
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs, context=3)
        result_lines = text.split("\n")
        # Should be lines 47..53 = 7 lines total
        assert len(result_lines) == 7
        assert result_lines[0] == "ctx 47"
        assert result_lines[3] == "FATAL crash"
        assert result_lines[6] == "ctx 53"


class TestFilterErrorContextIntegration:
    """Integration tests combining failed_step + filtering."""

    def test_failed_step_passes_through(self):
        """failed_step parameter filters to the relevant section first."""
        section_lines = ["===== TaskRun: build"] + [f"build {i}" for i in range(90)]
        section_lines[50] = "ERROR in build step"
        other = ["===== TaskRun: test"] + [f"test {i}" for i in range(20)]
        logs = "\n".join(section_lines + other)
        text, stats = filter_error_context(logs, failed_step="build")
        assert "ERROR in build step" in text
        assert stats["filtered"] is True

    def test_custom_context_parameter(self):
        lines = [f"x {i}" for i in range(100)]
        lines[50] = "ERROR boom"
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs, context=2)
        result_lines = text.split("\n")
        # Lines 48..52 = 5 lines
        assert len(result_lines) == 5
        assert result_lines[0] == "x 48"
        assert result_lines[2] == "ERROR boom"
        assert result_lines[4] == "x 52"

    def test_context_zero(self):
        """context=0 returns only the matching lines themselves."""
        lines = [f"x {i}" for i in range(100)]
        lines[30] = "ERROR one"
        lines[70] = "ERROR two"
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs, context=0)
        result_lines = [l for l in text.split("\n") if l != "  ..."]
        assert result_lines == ["ERROR one", "ERROR two"]

    def test_multiple_errors_same_line_counted_once(self):
        """A single line matching the regex is counted once, not per-group."""
        lines = [f"x {i}" for i in range(100)]
        # This line matches multiple groups but is one line
        lines[50] = "[ts] Error: segmentation fault"
        logs = "\n".join(lines)
        _, stats = filter_error_context(logs)
        assert stats["match_count"] == 1


# ===================================================================
# _ERROR_PATTERN regex
# ===================================================================

class TestErrorPatternTimestampBracket:
    """[timestamp] keyword patterns (first alternation)."""

    @pytest.mark.parametrize("keyword", [
        "Error", "error", "Failed", "failed", "Fatal", "fatal",
        "Warning", "warning", "denied", "timeout", "killed",
    ])
    def test_bracket_prefix_patterns(self, keyword):
        line = f"[2024-01-01T12:00:00Z] something {keyword} here"
        assert _ERROR_PATTERN.search(line) is not None


class TestErrorPatternLineStart:
    """Patterns anchored to start of line (second alternation)."""

    @pytest.mark.parametrize("prefix", [
        "Error", "ERROR", "ERRO", "FATAL", "WARNING", "WARN",
        "Traceback", "Exception", "panic",
    ])
    def test_line_start_keywords(self, prefix):
        line = f"{prefix}: something happened"
        assert _ERROR_PATTERN.search(line) is not None

    def test_step_pattern(self):
        assert _ERROR_PATTERN.search("STEP 5: RUN go build") is not None

    def test_step_different_digit(self):
        assert _ERROR_PATTERN.search("STEP 0 something") is not None

    def test_step_pattern_requires_digit(self):
        # "STEP X" should NOT match (no digit)
        # Actually the pattern is STEP [0-9] so "STEP X" won't match via ^STEP
        assert _ERROR_PATTERN.search("STEP X") is None


class TestErrorPatternInlineKeywords:
    """Inline keyword patterns (third alternation)."""

    @pytest.mark.parametrize("phrase", [
        "segmentation fault",
        "segfault",
        "sigsegv",
        "sigkill",
        "sigterm",
        "core dump",
        "out of memory",
        "oom",
        "exit code 1",
        "exit code 0",
        "exited with",
        "subprocess exited",
        "Skipping step because",
        "cannot find",
        "not found",
        "permission denied",
        "command not found",
        "no space",
        "disk full",
        "quota exceeded",
        "import error",
    ])
    def test_inline_patterns(self, phrase):
        line = f"some context before {phrase} and after"
        assert _ERROR_PATTERN.search(line) is not None

    def test_module_not_found(self):
        line = "module foo not found"
        assert _ERROR_PATTERN.search(line) is not None

    def test_module_not_found_with_dots(self):
        line = "module my.package.name not found"
        assert _ERROR_PATTERN.search(line) is not None

    def test_case_insensitive(self):
        """The regex is compiled with re.IGNORECASE."""
        assert _ERROR_PATTERN.search("SEGMENTATION FAULT") is not None
        assert _ERROR_PATTERN.search("Out Of Memory") is not None
        assert _ERROR_PATTERN.search("PERMISSION DENIED") is not None
        assert _ERROR_PATTERN.search("Disk Full") is not None

    def test_no_match_on_clean_line(self):
        assert _ERROR_PATTERN.search("everything is fine") is None

    def test_no_match_on_simple_words(self):
        assert _ERROR_PATTERN.search("compiling main.go") is None


# ===================================================================
# Edge cases and constants
# ===================================================================

class TestConstants:
    def test_context_lines_default(self):
        assert CONTEXT_LINES == 5


class TestEdgeCases:
    def test_single_line_log(self):
        text, stats = filter_error_context("just one line")
        assert text == "just one line"
        assert stats["total_lines"] == 1
        assert stats["filtered"] is False

    def test_all_lines_are_errors(self):
        """Every line matches -- one big merged range, no separators."""
        lines = [f"ERROR problem {i}" for i in range(100)]
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs)
        assert stats["match_count"] == 100
        assert "  ..." not in text
        # All lines present
        assert text == logs

    def test_adjacent_ranges_merge(self):
        """Ranges that are exactly adjacent (gap=0) should merge."""
        # With context=2, error at 10 covers [8..12], error at 15 covers [13..17]
        # start(15-2=13) <= end(12)+1=13, so they merge
        lines = [f"data {i}" for i in range(100)]
        lines[10] = "ERROR a"
        lines[15] = "ERROR b"
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs, context=2)
        assert stats["match_count"] == 2
        assert "  ..." not in text

    def test_ranges_one_apart_do_not_merge(self):
        """Ranges with a 1-line gap do NOT merge (start > end+1)."""
        # With context=2, error at 10 covers [8..12], error at 16 covers [14..18]
        # start(14) > end(12)+1=13 => separate ranges
        lines = [f"data {i}" for i in range(100)]
        lines[10] = "ERROR a"
        lines[16] = "ERROR b"
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs, context=2)
        assert stats["match_count"] == 2
        assert "  ..." in text

    def test_three_separate_ranges(self):
        """Three errors far apart produce two '  ...' separators."""
        lines = [f"data {i}" for i in range(200)]
        lines[10] = "ERROR first"
        lines[100] = "ERROR second"
        lines[190] = "ERROR third"
        logs = "\n".join(lines)
        text, stats = filter_error_context(logs)
        assert stats["match_count"] == 3
        assert text.count("  ...") == 2
