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
from decimal import Decimal
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

        def names(queue="normal"):
            return [r["nickname"] for r in self.api(path + "?queue=" + queue)]

        self.assertEqual(names(), ["小花猫", "C", "B"])
        delayed = self.api(path + "/" + a["id"], "PUT", {**a, "planned": "260301"})
        self.assertEqual(delayed["queue"], "deferred")
        self.assertEqual(names(), ["C", "B"])
        self.api(path + "/" + c["id"] + "/defer", "POST")
        self.api(path + "/" + a["id"] + "/defer", "POST")
        self.assertEqual(names("deferred"), ["小花猫", "C"])
        d = self.api(
            path + "?before=" + c["id"], "POST", {"nickname": "D", "queue": "deferred"}
        )
        self.assertEqual(names("deferred"), ["小花猫", "D", "C"])
        self.api(path + "/" + c["id"] + "/move", "POST", {"before": a["id"]})
        self.assertEqual(names("deferred"), ["C", "小花猫", "D"])
        with self.assertRaises(HTTPError):
            self.api(path + "/" + d["id"] + "/move", "POST", {"before": b["id"]})
        self.assertEqual(names(), ["B"])
        self.api(path + "/" + c["id"] + "/resume", "POST")
        self.assertEqual(names(), ["B", "C"])
        self.assertEqual(self.api("/stats")["count"], 2)
        self.assertIsNone(self.api("/stats")["until"])
        self.api(
            path + "/" + a["id"], "PUT", {"nickname": "小花猫", "status": "已结稿"}
        )
        self.assertEqual(names("deferred"), ["D"])
        from urllib.parse import quote

        for q in ["小hmao", "x花m", "小hu", "xiaohuamao", "xhm"]:
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
        self.assertIn(prev.year, stats["years"])
        by_year = self.api(f"/stats?year={prev.year}")
        self.assertEqual(by_year["monthly"][prev.strftime("%Y-%m")], 1.05)
        self.assertEqual(stats["total"], 2.05)
        self.assertEqual(stats["monthly"][prev.strftime("%Y-%m")], 1.05)
        self.assertEqual(stats["current"], 1)
        self.assertEqual(stats["average"], 1.02)
        self.assertEqual(stats["platforms"], {"微信": 1.05, "支付宝": 1})

    def test_client_history_and_all_payments(self):
        now = today()
        account = {"nickname": "小花猫", "contact_type": "QQ", "contact": "demo-A"}
        self.api(
            "/commissions",
            "POST",
            {
                **account,
                "status": "已结稿",
                "payments": [{"amount": 45, "date": "260101"}],
            },
        )
        self.api(
            "/commissions",
            "POST",
            {
                **account,
                "nickname": "另一昵称",
                "queue": "deferred",
                "payments": [{"amount": 15, "date": now.isoformat()}],
            },
        )
        self.api(
            "/commissions", "POST", {"nickname": "小花猫", "payments": [{"amount": 10}]}
        )

        def card():
            return next(r for r in self.api("/commissions") if r["status"] == "已结稿")

        self.assertEqual(Decimal(card()["history"]["total"]), 70)
        self.api(
            "/commissions",
            "POST",
            {**account, "contact": "demo-B", "payments": [{"amount": 100}]},
        )
        self.api(
            "/commissions",
            "POST",
            {
                **account,
                "payments": [
                    {"amount": 200, "date": (now + timedelta(days=1)).isoformat()}
                ],
            },
        )
        history = card()["history"]
        self.assertEqual(Decimal(history["total"]), 60)
        self.assertEqual(history["completed"], 1)
        self.assertEqual(history["latest"], now.isoformat())
        entries = self.api("/payments")
        self.assertEqual(len(entries), 5)
        self.assertEqual(
            [r["date"] for r in entries],
            sorted([r["date"] for r in entries], reverse=True),
        )

    def test_status_actions(self):
        for lane in ("normal", "deferred"):
            record = self.api("/commissions", "POST", {
                "nickname": lane, "queue": lane, "payments": [{"amount": 20}]
            })
            path = "/commissions/" + record["id"]
            for target in ("进行中", "进行中", "已结稿", "已结稿"):
                self.api(path + "/status", "PATCH", {"status": target})
                self.assertEqual(self.api(path), {**record, "status": target})
            with self.assertRaises(HTTPError) as error:
                self.api(path + "/status", "PATCH", {"status": "进行中"})
            self.assertEqual(error.exception.code, 409)
            self.assertEqual(self.api("/commissions?queue=" + lane), [])

    def test_examples_and_legacy_backup(self):
        self.api(
            "/import",
            "POST",
            {
                "version": 1,
                "settings": Settings().model_dump(),
                "commissions": [
                    {
                        "id": "legacy",
                        "nickname": "雾岛",
                        "contact_type": "微信",
                        "contact": "demo_wudao",
                    }
                ],
            },
        )
        self.assertEqual(self.api("/commissions?queue=normal")[0]["id"], "legacy")
        self.api("/examples", "POST")
        snapshot = self.api("/export")
        self.api("/examples", "POST")
        self.assertEqual(self.api("/export"), snapshot)
        self.assertEqual(len(self.api("/payments")), 8)
        self.assertEqual(self.api("/commissions/legacy")["queue"], "normal")
        legacy = next(r for r in self.api("/commissions") if r["id"] == "legacy")
        shared = [r for r in self.api("/commissions") if r["contact"] == "demo_wudao"]
        self.assertGreater(len(shared), 1)
        self.assertGreater(Decimal(legacy["history"]["total"]), 0)
        self.assertTrue(all(r["history"] == legacy["history"] for r in shared))
        self.api("/import", "POST", snapshot)
        self.assertEqual(self.api("/export"), snapshot)
        self.api("/examples", "DELETE")
        self.assertEqual([r["id"] for r in self.api("/commissions")], ["legacy"])
        self.assertEqual(self.api("/payments"), [])

    def test_backup_preserves_images_and_rolls_back_invalid_import(self):
        stream = BytesIO()
        Image.new("RGB", (900, 540), "blue").save(stream, format="PNG")
        data = base64.b64encode(stream.getvalue()).decode()
        r = self.api(
            "/commissions",
            "POST",
            {
                "title": "角色设定",
                "requirements": "完整需求",
                "attachments": [{"name": "参考图", "data": data}],
            },
        )
        snapshot = self.api("/export")
        self.api("/commissions/" + r["id"], "DELETE")
        self.api("/import", "POST", snapshot)
        self.assertEqual(self.api("/export"), snapshot)
        self.assertEqual(
            self.api("/images/" + r["attachments"][0]["id"]), stream.getvalue()
        )
        thumb = self.api("/images/" + r["attachments"][0]["id"] + "?thumbnail=true")
        with Image.open(BytesIO(thumb)) as preview:
            self.assertEqual(preview.size, (240, 144))
        saved = self.api("/commissions/" + r["id"])
        self.assertEqual(saved["title"], "角色设定")
        self.assertEqual(saved["requirements"], "完整需求")
        snapshot["commissions"][0]["attachments"][0]["data"] = "broken"
        with self.assertRaises(HTTPError) as error:
            self.api("/import", "POST", snapshot)
        self.assertEqual(error.exception.code, 422)
        self.assertEqual(
            self.api("/export")["commissions"][0]["attachments"][0]["data"], data
        )


if __name__ == "__main__":
    unittest.main()
