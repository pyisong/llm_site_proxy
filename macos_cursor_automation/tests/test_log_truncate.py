"""日志截断。"""

import logging
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from log_utils import TruncatingAccessFormatter, TruncatingLogFormatter, truncate_log_text


class TruncateLogTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = os.environ.get("CURSOR_BRIDGE_LOG_MAX_CHARS")

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("CURSOR_BRIDGE_LOG_MAX_CHARS", None)
        else:
            os.environ["CURSOR_BRIDGE_LOG_MAX_CHARS"] = self._prev

    def test_no_limit_when_zero(self) -> None:
        os.environ["CURSOR_BRIDGE_LOG_MAX_CHARS"] = "0"
        s = "x" * 20000
        self.assertEqual(truncate_log_text(s), s)

    def test_truncates_keeps_head_and_tail(self) -> None:
        os.environ["CURSOR_BRIDGE_LOG_MAX_CHARS"] = "120"
        s = "HEAD" + ("m" * 500) + "TAIL"
        out = truncate_log_text(s)
        self.assertTrue(out.startswith("HEAD"))
        self.assertTrue(out.endswith("TAIL"))
        self.assertIn("[省略中间", out)
        self.assertIn("total=508", out)
        self.assertLessEqual(len(out), 120)

    def test_formatter_truncates_long_line(self) -> None:
        os.environ["CURSOR_BRIDGE_LOG_MAX_CHARS"] = "80"
        fmt = TruncatingLogFormatter("%(message)s")
        record = logging.LogRecord("t", 20, "", 0, "A" + ("x" * 200) + "Z", (), None)
        out = fmt.format(record)
        self.assertIn("[省略中间", out)
        self.assertTrue(out.startswith("A"))
        self.assertTrue(out.endswith("Z"))
        self.assertLessEqual(len(out), 80)

    def test_formatter_accepts_uvicorn_use_colors(self) -> None:
        fmt = TruncatingLogFormatter("%(message)s", use_colors=True)
        record = logging.LogRecord("t", 20, "", 0, "ok", (), None)
        self.assertEqual(fmt.format(record), "ok")

    def test_uvicorn_log_config_dictconfig(self) -> None:
        import logging.config
        import tempfile

        from cursor_automation import build_serve_uvicorn_log_config

        with tempfile.TemporaryDirectory() as d:
            cfg = build_serve_uvicorn_log_config(Path(d))
            logging.config.dictConfig(cfg)
            logging.getLogger("uvicorn").info("uvicorn log ok")
            trunc = cfg["formatters"]["file_trunc_access"]
            fmt_cls = trunc["()"]
            fmt = fmt_cls(
                fmt=trunc["fmt"],
                datefmt=trunc["datefmt"],
                use_colors=trunc.get("use_colors", False),
            )
            rec = logging.LogRecord(
                "uvicorn.access",
                20,
                "",
                0,
                '%s - "%s %s HTTP/%s" %d',
                ("127.0.0.1:8000", "GET", "/", "1.1", 200),
                None,
            )
            self.assertIn("GET /", fmt.format(rec))
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %d',
                "127.0.0.1:8000",
                "GET",
                "/health",
                "1.1",
                200,
            )


if __name__ == "__main__":
    unittest.main()
