"""Unit tests for RMR Pydantic v2 data contracts and security invariants (TASK-02)."""

import unittest

from pydantic import ValidationError

from rmr.schemas import (
    EvidenceItem,
    EvidenceTarget,
    InspectEvidenceOutput,
    InvestigateInput,
    ProjectConfig,
    TreeState,
)


class TestSchemas(unittest.TestCase):
    """Test suite for data models, validation rules, and schema invariants."""

    # --- TreeState ---
    def test_tree_state_valid(self):
        state = TreeState(
            head_commit="a1b2c3d",
            is_dirty=False,
            worktree_fingerprint="f1e2d3c4b5a6",
        )
        data = state.model_dump()
        self.assertEqual(data["head_commit"], "a1b2c3d")
        self.assertFalse(data["is_dirty"])
        self.assertEqual(data["worktree_fingerprint"], "f1e2d3c4b5a6")

    def test_tree_state_missing_field(self):
        with self.assertRaises(ValidationError):
            TreeState(head_commit="a1b2c3d", is_dirty=True)  # missing fingerprint

    # --- ProjectConfig ---
    def test_project_config_defaults(self):
        cfg = ProjectConfig(project_id="rmr", root_path=".")
        self.assertEqual(cfg.language, "python")
        self.assertEqual(cfg.entry_docs, [])

    def test_project_config_custom(self):
        cfg = ProjectConfig(
            project_id="frontend",
            root_path="./web",
            language="typescript",
            entry_docs=["README.md", "docs/spec.md"],
        )
        self.assertEqual(cfg.project_id, "frontend")
        self.assertEqual(cfg.language, "typescript")
        self.assertEqual(len(cfg.entry_docs), 2)

    def test_project_config_missing_required(self):
        with self.assertRaises(ValidationError):
            ProjectConfig(project_id="rmr")  # missing root_path

    # --- InvestigateInput ---
    def test_investigate_input_valid(self):
        inp = InvestigateInput(project_id="rmr", mode="outline")
        self.assertEqual(inp.query, "")
        self.assertIsNone(inp.subpath)
        self.assertEqual(inp.max_results, 30)

        for mode in ("outline", "symbol", "grep", "ast"):
            obj = InvestigateInput(project_id="rmr", mode=mode)
            self.assertEqual(obj.mode, mode)

    def test_investigate_input_invalid_mode(self):
        with self.assertRaises(ValidationError):
            InvestigateInput(project_id="rmr", mode="invalid_mode")

    def test_investigate_input_max_results_bounds(self):
        # ge=1, le=50
        with self.assertRaises(ValidationError):
            InvestigateInput(project_id="rmr", mode="grep", max_results=0)

        with self.assertRaises(ValidationError):
            InvestigateInput(project_id="rmr", mode="grep", max_results=51)

        valid_min = InvestigateInput(project_id="rmr", mode="grep", max_results=1)
        self.assertEqual(valid_min.max_results, 1)

        valid_max = InvestigateInput(project_id="rmr", mode="grep", max_results=50)
        self.assertEqual(valid_max.max_results, 50)

    # --- EvidenceTarget ---
    def test_evidence_target_defaults(self):
        target = EvidenceTarget(kind="file_lines", path="src/rmr/schemas.py")
        self.assertEqual(target.base_ref, "HEAD")
        self.assertEqual(target.target_ref, "WORKTREE")
        self.assertIsNone(target.start_line)
        self.assertIsNone(target.end_line)
        self.assertIsNone(target.symbol_name)

    def test_evidence_target_kinds(self):
        for kind in ("file_lines", "symbol", "git_diff"):
            target = EvidenceTarget(kind=kind, path="file.py")
            self.assertEqual(target.kind, kind)

        with self.assertRaises(ValidationError):
            EvidenceTarget(kind="invalid_kind", path="file.py")

    def test_evidence_target_line_bounds(self):
        with self.assertRaises(ValidationError):
            EvidenceTarget(kind="file_lines", path="file.py", start_line=0)

        target = EvidenceTarget(
            kind="file_lines", path="file.py", start_line=1, end_line=100
        )
        self.assertEqual(target.start_line, 1)
        self.assertEqual(target.end_line, 100)

    # --- EvidenceItem & InspectEvidenceOutput ---
    def test_evidence_item_and_output(self):
        item = EvidenceItem(
            kind="file_lines",
            path="src/rmr/schemas.py",
            line_range=(1, 50),
            content="print('hello')",
            content_sha256="abc123sha",
            truncated=False,
        )
        self.assertEqual(item.kind, "file_lines")
        self.assertFalse(item.truncated)

        state = TreeState(
            head_commit="1234567",
            is_dirty=False,
            worktree_fingerprint="aabbccddeeff",
        )
        output = InspectEvidenceOutput(
            project_id="rmr",
            tree_state=state,
            evidence_items=[item],
        )
        dumped = output.model_dump()
        self.assertEqual(dumped["project_id"], "rmr")
        self.assertEqual(len(dumped["evidence_items"]), 1)
        self.assertEqual(dumped["tree_state"]["head_commit"], "1234567")



if __name__ == "__main__":
    unittest.main()
