"""工作区隔离：session_id / force 解析。"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai_bridge import (  # noqa: E402
    _agent_force_from_metadata,
    _job_workspace_dir,
    _normalize_workspace,
    _session_id_from_metadata,
)


class WorkspaceIsolateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = os.environ.get("CURSOR_BRIDGE_ISOLATE_WORKSPACE")
        os.environ["CURSOR_BRIDGE_ISOLATE_WORKSPACE"] = "1"

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("CURSOR_BRIDGE_ISOLATE_WORKSPACE", None)
        else:
            os.environ["CURSOR_BRIDGE_ISOLATE_WORKSPACE"] = self._prev

    def test_sanitize_req_id(self) -> None:
        p = _job_workspace_dir(Path("/workspace"), "e9a2/ae14")
        self.assertEqual(p, Path("/workspace/jobs/e9a2_ae14"))

    def test_empty_req_id_fallback(self) -> None:
        p = _job_workspace_dir(Path("/workspace"), "  ")
        self.assertEqual(p, Path("/workspace/jobs/unknown"))

    def test_session_id_prefers_metadata_over_req_id(self) -> None:
        body = {"metadata": {"session_id": "task-abc"}}
        ws = _normalize_workspace(body, Path("/workspace"), req_id="http-req-uuid")
        self.assertEqual(ws, Path("/workspace/jobs/task-abc"))

    def test_new_session_id_new_dir(self) -> None:
        a = _normalize_workspace(
            {"metadata": {"session_id": "task-1"}}, Path("/workspace"), req_id="r1"
        )
        b = _normalize_workspace(
            {"metadata": {"session_id": "task-2"}}, Path("/workspace"), req_id="r1"
        )
        self.assertEqual(a, Path("/workspace/jobs/task-1"))
        self.assertEqual(b, Path("/workspace/jobs/task-2"))
        self.assertNotEqual(a, b)

    def test_fallback_req_id_without_session(self) -> None:
        ws = _normalize_workspace({}, Path("/workspace"), req_id="only-req")
        self.assertEqual(ws, Path("/workspace/jobs/only-req"))

    def test_session_id_from_aliases(self) -> None:
        self.assertEqual(
            _session_id_from_metadata({"task_id": "t1"}),
            "t1",
        )
        self.assertEqual(
            _session_id_from_metadata({"cursor_session_id": "c1"}),
            "c1",
        )

    def test_force_default_true(self) -> None:
        self.assertTrue(_agent_force_from_metadata({}, default=True))
        self.assertTrue(_agent_force_from_metadata({"force": True}))
        self.assertFalse(_agent_force_from_metadata({"force": False}))
        self.assertFalse(_agent_force_from_metadata({"force": "false"}))


if __name__ == "__main__":
    unittest.main()
