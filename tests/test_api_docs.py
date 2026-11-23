"""Keep the public guide's parameter tables in step with the actual API."""
import re
import unittest

from app import GUIDE_SECTIONS, app


def body_fields(schema, models, prefix=""):
    if "$ref" in schema:
        schema = models[schema["$ref"].rsplit("/", 1)[1]]
    if schema.get("type") == "array":
        yield from body_fields(schema["items"], models, prefix + "[]")
    for key, child in schema.get("properties", {}).items():
        name = f"{prefix}.{key}" if prefix else key
        yield name
        yield from body_fields(child, models, name)


class ApiGuideTest(unittest.TestCase):
    def test_every_operation_documents_all_inputs(self):
        spec = app.openapi()
        for path, methods in spec["paths"].items():
            for method, operation in methods.items():
                heading = f"{method.upper()} {path}"
                with self.subTest(heading=heading):
                    text = operation["description"]
                    self.assertEqual(text, GUIDE_SECTIONS[heading])
                    purpose, workflow, table = text.split("\n\n")
                    self.assertTrue(purpose.strip())
                    self.assertTrue(workflow.startswith("**页面操作**"))
                    self.assertTrue(table.startswith("| 参数名 | 参数格式 | 参数码值（值 — 含义） | 是否必填项 |"))
                    expected = {param["name"] for param in operation.get("parameters", [])}
                    if "requestBody" in operation:
                        schema = operation["requestBody"]["content"]["application/json"]["schema"]
                        expected.update(body_fields(schema, spec["components"]["schemas"]))
                    documented = re.findall(r"^\| `([^`]+)` \|", table, re.MULTILINE)
                    self.assertCountEqual(documented, expected)
                    if not expected:
                        self.assertIn("| 无 |", table)
