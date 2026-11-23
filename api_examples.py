"""Named OpenAPI request examples for Swagger UI and API clients."""


def example(summary, value, description=None):
    result = {"summary": summary, "value": value}
    if description:
        result["description"] = description
    return result


AGREEMENT = {"stage": "尾款", "amount": "600.00", "date": None}
DEPOSIT = {"stage": "定金", "amount": "200.00", "date": "2026-02-28", "platform": "微信"}
OPTIONS = {"contacts": ["QQ", "微信"], "platforms": ["微信", "支付宝"]}
ORDER = {"nickname": "小林", "title": "角色头像", "requirements": "蓝色背景",
         "contact_type": "QQ", "contact": "demo-account", "payments": [DEPOSIT, AGREEMENT]}
EDIT = {**ORDER, "revision": "替换为GET详情返回的revision", "attachments": []}
EDIT_NOTE = "从最新 GET 详情构造完整请求，保留本次没有修改的字段、全部其他稿费及附件 ID。"

CREATE = {
    "queue": example("新建排队约稿", {"nickname": "小林", "title": "角色头像"}),
    "mixed": example("已收定金并约定尾款", ORDER),
    "deferred": example("直接加入延期队列", {"nickname": "小许", "queue": "deferred"}),
}
UPDATE = {
    "settle": example("给原尾款补填日期", {**EDIT, "payments": [DEPOSIT, {**AGREEMENT, "date": "2026-03-10"}]}, EDIT_NOTE),
    "mark": example("为已有约稿添加插队标记", {**EDIT, "is_priority": True}, EDIT_NOTE),
    "attachment": example("保留已有参考图", {**EDIT, "attachments": [
        {"id": "替换为本约稿已有附件ID", "name": "角色参考图"}]}, EDIT_NOTE),
}
PAYMENT = {
    "deposit": example("登记定金", DEPOSIT),
    "agreement": example("登记未付款约定", AGREEMENT),
    "today": example("按上海当天日期收款", {"amount": "180.00", "stage": "稿费"}),
}
MOVE = {"before": example("移到同队另一单之前", {"before": "替换为目标约稿ID"}),
        "end": example("移到队尾", {"before": None})}
STATUS = {"start": example("未开始转为进行中", {"status": "进行中"}),
          "finish": example("进行中转为已结稿", {"status": "已结稿"})}
SETTINGS = {"options": example("设置联系方式与收款平台", OPTIONS, "完整替换两个列表，首项是默认值。")}
IMPORT = {"restore": example("还原一份包含约定的快照", {
    "version": 1, "settings": OPTIONS, "commissions": [{**ORDER, "id": "demo-order-1"}]},
    "导入会替换现有数据。恢复时提交 GET /api/export 下载的完整 JSON。")}
