"""工作区 req_id 路径消毒（不依赖 fastapi）。"""

import re
import unittest
from pathlib import Path

_REQ_ID_DIR_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _job_workspace_dir(default: Path, req_id: str) -> Path:
    safe = _REQ_ID_DIR_SAFE.sub("_", (req_id or "").strip()).strip("._") or "unknown"
    return default.expanduser().resolve() / "jobs" / safe


class WorkspaceIsolateTests(unittest.TestCase):
    def test_sanitize_req_id(self) -> None:
        p = _job_workspace_dir(Path("/workspace"), "e9a2/ae14")
        self.assertEqual(p, Path("/workspace/jobs/e9a2_ae14"))

    def test_empty_req_id_fallback(self) -> None:
        p = _job_workspace_dir(Path("/workspace"), "  ")
        self.assertEqual(p, Path("/workspace/jobs/unknown"))


if __name__ == "__main__":
    unittest.main()
