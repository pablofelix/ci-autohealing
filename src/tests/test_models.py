"""Unit tests for data models."""

import unittest
from models import BuildStatus, TaskRun, PipelineRun, Component, ScanResult


class TestBuildStatus(unittest.TestCase):
    """Test BuildStatus enum."""

    def test_status_values(self):
        """Test that BuildStatus has expected values."""
        self.assertEqual(BuildStatus.FAILED.value, "Failed")
        self.assertEqual(BuildStatus.SUCCEEDED.value, "Succeeded")
        self.assertEqual(BuildStatus.RUNNING.value, "Running")
        self.assertEqual(BuildStatus.PENDING.value, "Pending")


class TestTaskRun(unittest.TestCase):
    """Test TaskRun model."""

    def test_create_taskrun(self):
        """Test TaskRun creation."""
        tr = TaskRun(
            name="my-taskrun",
            pod_name="my-pod",
            failed_steps=["build", "push"],
            exit_code=1
        )
        self.assertEqual(tr.name, "my-taskrun")
        self.assertEqual(tr.pod_name, "my-pod")
        self.assertEqual(tr.failed_steps, ["build", "push"])
        self.assertEqual(tr.exit_code, 1)

    def test_taskrun_defaults(self):
        """Test TaskRun with default values."""
        tr = TaskRun(name="test")
        self.assertIsNone(tr.pod_name)
        self.assertEqual(tr.failed_steps, [])
        self.assertIsNone(tr.exit_code)


class TestPipelineRun(unittest.TestCase):
    """Test PipelineRun model."""

    def test_create_pipelinerun(self):
        """Test PipelineRun creation."""
        pr = PipelineRun(
            name="my-pr",
            uid="550e8400-e29b-41d4-a716-446655440000",
            namespace="NAMESPACE_PLACEHOLDER",
            component="my-component",
            repository="org/repo",
            repository_url="https://github.com/org/repo",
            branch="main",
            status=BuildStatus.FAILED
        )
        self.assertEqual(pr.name, "my-pr")
        self.assertEqual(pr.uid, "550e8400-e29b-41d4-a716-446655440000")
        self.assertEqual(pr.namespace, "NAMESPACE_PLACEHOLDER")
        self.assertEqual(pr.component, "my-component")
        self.assertEqual(pr.status, BuildStatus.FAILED)

    def test_konflux_logs_url(self):
        """Test Konflux logs URL generation."""
        pr = PipelineRun(
            name="test-pr",
            uid="123",
            namespace="test-ns",
            component="test",
            repository="test",
            repository_url="test",
            branch="test",
            status=BuildStatus.FAILED
        )
        url = pr.konflux_logs_url
        self.assertIn("test-ns", url)
        self.assertIn("test-pr", url)
        self.assertIn("{app}", url)  # Placeholder not yet replaced

    def test_has_logs_false(self):
        """Test has_logs when no logs present."""
        pr = PipelineRun(
            name="test",
            uid="123",
            namespace="test",
            component="test",
            repository="test",
            repository_url="test",
            branch="test",
            status=BuildStatus.FAILED
        )
        self.assertFalse(pr.has_logs)

    def test_has_logs_true(self):
        """Test has_logs when logs present."""
        pr = PipelineRun(
            name="test",
            uid="123",
            namespace="test",
            component="test",
            repository="test",
            repository_url="test",
            branch="test",
            status=BuildStatus.FAILED,
            build_logs="Some log content"
        )
        self.assertTrue(pr.has_logs)

    def test_has_logs_empty_string(self):
        """Test has_logs with empty string."""
        pr = PipelineRun(
            name="test",
            uid="123",
            namespace="test",
            component="test",
            repository="test",
            repository_url="test",
            branch="test",
            status=BuildStatus.FAILED,
            build_logs=""
        )
        self.assertFalse(pr.has_logs)


class TestComponent(unittest.TestCase):
    """Test Component model."""

    def test_create_component(self):
        """Test Component creation."""
        comp = Component(
            name="my-component",
            repository_url="https://github.com/org/repo",
            branch="main",
            namespace="NAMESPACE_PLACEHOLDER"
        )
        self.assertEqual(comp.name, "my-component")
        self.assertEqual(comp.repository_url, "https://github.com/org/repo")
        self.assertEqual(comp.branch, "main")
        self.assertEqual(comp.namespace, "NAMESPACE_PLACEHOLDER")

    def test_from_file(self):
        """Test loading components from file."""
        import tempfile
        import os

        # Create temporary file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("# This is a comment\n")
            f.write("component-1\n")
            f.write("\n")  # Empty line
            f.write("component-2\n")
            f.write("# Another comment\n")
            f.write("component-3\n")
            temp_file = f.name

        try:
            components = Component.from_file(temp_file, "test-namespace")
            self.assertEqual(len(components), 3)
            self.assertEqual(components[0].name, "component-1")
            self.assertEqual(components[1].name, "component-2")
            self.assertEqual(components[2].name, "component-3")
            for comp in components:
                self.assertEqual(comp.namespace, "test-namespace")
                self.assertEqual(comp.repository_url, "")
                self.assertEqual(comp.branch, "")
        finally:
            os.unlink(temp_file)


class TestScanResult(unittest.TestCase):
    """Test ScanResult model."""

    def test_create_scan_result(self):
        """Test ScanResult creation."""
        result = ScanResult(
            scan_id="550e8400-e29b-41d4-a716-446655440000",
            components_scanned=10,
            failures_found=5,
            new_failures=2,
            logs_fetched=3,
            duration_seconds=45.5
        )
        self.assertEqual(result.scan_id, "550e8400-e29b-41d4-a716-446655440000")
        self.assertEqual(result.components_scanned, 10)
        self.assertEqual(result.failures_found, 5)
        self.assertEqual(result.new_failures, 2)
        self.assertEqual(result.logs_fetched, 3)
        self.assertEqual(result.duration_seconds, 45.5)


if __name__ == '__main__':
    unittest.main()
