"""Regression checks for queue changes, accounting and transactional backup restore."""

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from app import Settings, today


class WorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}/api"
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app:app",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=root,
            env={**os.environ, "COMMISSION_DB": str(Path(directory.name) / "test.db")},
        )
        cls.addClassCleanup(process.wait)
        cls.addClassCleanup(process.terminate)
        for _ in range(100):
            try:
                with urlopen(cls.base + "/settings", timeout=1):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("Test server failed to start")

    def api(self, path, method="GET", data=None):
        payload = json.dumps(data).encode() if data is not None else None
        req = Request(
            self.base + path,
            payload,
            {"Content-Type": "application/json"},
            method=method,
        )
        with urlopen(req, timeout=5) as response:
            raw = response.read()
            return (
                json.loads(raw)
                if raw and response.headers.get_content_type() == "application/json"
                else raw
            )

    def setUp(self):
        self.api(
            "/import",
            "POST",
            {"version": 1, "settings": Settings().model_dump(), "commissions": []},
        )

    def test_queue_and_mixed_search(self):
        path = "/commissions"
        a = self.api(path, "POST", {"nickname": "小花猫", "planned": "260228"})
        b = self.api(path, "POST", {"nickname": "B"})
        c = self.api(path + "?before=" + b["id"], "POST", {"nickname": "C"})
        names = lambda: [
            r["nickname"] for r in self.api(path) if r["status"] != "已结稿"
        ]
        self.assertEqual(names(), ["小花猫", "C", "B"])
        self.api(path + "/" + a["id"], "PUT", {**a, "planned": "260301"})
        self.assertEqual(names(), ["C", "小花猫", "B"])
        self.api(path + "/" + c["id"] + "/defer", "POST")
        self.assertEqual(names(), ["小花猫", "C", "B"])
        self.api(path + "/" + b["id"] + "/move", "POST", {"before": a["id"]})
        self.assertEqual(names(), ["B", "小花猫", "C"])
        self.api(path + "/" + a["id"], "PUT", {**a, "status": "已结稿"})
        self.assertEqual(names(), ["B", "C"])
        from urllib.parse import quote

        for q in ["小hmao", "x花m", "xiaohuamao", "xhm"]:
            self.assertEqual(
                self.api("/clients?q=" + quote(q))[0]["nickname"], "小花猫"
            )
        self.api(path + "/" + a["id"], "PUT", a)
        self.assertEqual(names(), ["B", "C", "小花猫"])

    def test_receipts_and_concurrent_appends(self):
        now = today()
        prev = now.replace(day=1) - timedelta(days=1)
        r = self.api(
            "/commissions",
            "POST",
            {"payments": [{"amount": "1.05", "date": prev.isoformat()}]},
        )
        target = "/commissions/" + r["id"] + "/payments"

        def add(_):
            return self.api(
                target,
                "POST",
                {"platform": "支付宝", "amount": "0.10", "date": now.isoformat()},
            )

        with ThreadPoolExecutor(4) as executor:
            list(executor.map(add, range(10)))
        self.api(
            target,
            "POST",
            {"amount": 999, "date": (now + timedelta(days=1)).isoformat()},
        )
        stats = self.api("/stats")
        self.assertEqual(stats["total"], 2.05)
        self.assertEqual(stats["monthly"][prev.strftime("%Y-%m")], 1.05)
        self.assertEqual(stats["current"], 1)
        self.assertEqual(stats["average"], 1.02)
        self.assertEqual(stats["platforms"], {"微信": 1.05, "支付宝": 1})

    def test_backup_preserves_images_and_rolls_back_invalid_import(self):
        stream = BytesIO()
        Image.new("RGB", (4, 4), "blue").save(stream, format="PNG")
        data = base64.b64encode(stream.getvalue()).decode()
        r = self.api(
            "/commissions", "POST", {"attachments": [{"name": "参考图", "data": data}]}
        )
        snapshot = self.api("/export")
        self.api("/commissions/" + r["id"], "DELETE")
        self.api("/import", "POST", snapshot)
        self.assertEqual(self.api("/export"), snapshot)
        self.assertEqual(
            self.api("/images/" + r["attachments"][0]["id"]), stream.getvalue()
        )
        snapshot["commissions"][0]["attachments"][0]["data"] = "broken"
        with self.assertRaises(HTTPError) as error:
            self.api("/import", "POST", snapshot)
        self.assertEqual(error.exception.code, 422)
        self.assertEqual(
            self.api("/export")["commissions"][0]["attachments"][0]["data"], data
        )


if __name__ == "__main__":
    unittest.main()
