"""Named OpenAPI request examples for Swagger UI and API clients."""


def example(summary, value, description=None):
    result = {"summary": summary, "value": value}
    if description:
        result["description"] = description
    return result


AGREEMENT = {"stage": "尾款", "amount": "600.00", "date": None, "platform": "微信"}
DEPOSIT = {"stage": "定金", "amount": "200.00", "date": "2026-02-28", "platform": "微信"}
OPTIONS = {"contacts": ["QQ", "微信"], "platforms": ["微信", "支付宝"]}
ORDER = {"nickname": "小林", "title": "角色头像", "requirements": "蓝色背景",
         "contact_type": "QQ", "contact": "demo-account", "payments": [DEPOSIT, AGREEMENT]}
EDIT = {**ORDER, "revision": "<revision-from-GET>", "status": "进行中", "queue": "normal",
        "planned": "2026-03-01", "started": "2026-03-02", "is_priority": False,
        "attachments": [{"id": "<existing-attachment-id>", "name": "角色参考图"}]}
EDIT_NOTE = "这是虚构约稿的完整编辑后示例。对真实单先 GET，再只修改目标字段；不要把此模板的日期、状态、款项或附件列表复制到真实单。"

CREATE = {
    "queue": example("新建排队约稿", {"nickname": "小林", "title": "角色头像"}),
    "mixed": example("填写联系账号、排期与稿费", {**ORDER, "queue": "normal", "status": "进行中",
                                               "planned": "2026-03-01", "started": "2026-03-02"}),
    "archived": example("补录已结稿的历史约稿", {**ORDER, "status": "已结稿", "payments": [DEPOSIT]}),
    "insert": example("在指定约稿前新建一单", {"nickname": "小许", "queue": "normal"},
                      "在上方 Parameters 的 before 填同队目标约稿 ID，再执行请求。"),
    "deferred": example("直接加入延期队列", {"nickname": "小许", "queue": "deferred"}),
}
UPDATE = {
    "settle": example("给原尾款补填日期", {**EDIT, "payments": [DEPOSIT, {**AGREEMENT, "date": "2026-03-10"}]}, EDIT_NOTE),
    "mark": example("为已有约稿添加插队标记", {**EDIT, "is_priority": True}, EDIT_NOTE),
    "attachment": example("更改需求并保留已有参考图", {**EDIT, "requirements": "蓝色背景，增加云朵"}, EDIT_NOTE),
}
PAYMENT = {
    "deposit": example("登记定金", DEPOSIT),
    "agreement": example("登记未付款约定", AGREEMENT),
    "today": example("按上海当天日期收款", {"amount": "180.00", "stage": "稿费"}),
}
MOVE = {"end": example("移到队尾", {}, "省略 before 或显式传入 null 都表示队尾。"),
        "before": example("移到同队另一单之前", {"before": "<target-commission-id>"}, "先读取同队列表，再替换为真实的目标 ID。")}
STATUS = {"start": example("未开始转为进行中", {"status": "进行中"}),
          "finish": example("进行中转为已结稿", {"status": "已结稿"})}
SETTINGS = {"options": example("设置联系方式与收款平台", OPTIONS, "完整替换两个列表，首项是默认值。")}
IMPORT = {"restore": example("还原一份包含约定的快照", {
    "version": 1, "settings": OPTIONS, "commissions": [{**ORDER, "id": "demo-order-1"}]},
    "导入会替换现有数据。恢复时提交 GET /api/export 下载的完整 JSON。")}

# One fictional record used throughout the JSON response examples.
DETAIL = {**EDIT, "id": "demo-order-1", "revision": "0123456789abcdef0123456789abcdef",
          "brief_received": True, "sample": False,
          "payments": [DEPOSIT, {**AGREEMENT, "date": None, "platform": "微信"}],
          "attachments": [{"id": "demo-image", "name": "角色参考图"}]}
CARD = {**DETAIL, "history": {"completed": 0, "total": "200.00", "latest": "2026-02-28"},
        "agreed_total": "600.00"}
RECEIPT = {**DEPOSIT, **{key: DETAIL[key] for key in ("title", "nickname", "sample", "is_priority")},
           "commission_id": DETAIL["id"]}
STATS = {"count": 1, "deferred_count": 0, "until": "2026-03-01", "total": 200.0,
         "average": 100.0, "current": 0.0, "years": [2026], "platforms": {"微信": 200.0},
         "monthly": {"2026-02": 200.0, "2026-03": 0.0},
         "periods": {"3": {"total": 200.0, "average": 66.67}, "6": {"total": 200.0, "average": 33.33},
                     "12": {"total": 200.0, "average": 16.67}}}
WORKSPACE = {"page": "overview", "settings": OPTIONS, "records": [DETAIL], "receipts": [],
             "count": 1, "has_samples": False, "stats": STATS}
