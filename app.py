"""稿序 · 约稿队列与收入管理"""

import base64
import binascii
import hashlib
import csv
import inspect
import math
import json
import os
import re
import sqlite3
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from importlib.resources import files
from io import BytesIO, StringIO
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

import api_examples

from fastapi import Body, FastAPI, Header, HTTPException, Path as PathParam, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.routing import Match
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, BeforeValidator, Field, WithJsonSchema, computed_field, field_validator, model_serializer
from pypinyin import lazy_pinyin

ROOT = Path(__file__).parent
DB = Path(os.getenv("COMMISSION_DB", ROOT / "var" / "studio.sqlite3"))
# Reuse the published guide sections in the corresponding Swagger operations.
GUIDE = (ROOT / "llms.txt").read_text(encoding="utf-8")
GUIDE_SECTIONS = dict(section.split("\n", 1) for section in GUIDE.split("\n## ")[1:])


def documented(*sections):
    def decorate(endpoint):
        parts = [inspect.getdoc(endpoint) or ""]
        parts.extend(GUIDE_SECTIONS[section].strip() for section in sections)
        endpoint.__doc__ = "\n\n".join(filter(None, parts))
        return endpoint
    return decorate


MAX_IMAGE = 12 * 1024 * 1024


def today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def parse_date(value):
    if isinstance(value, str):
        value = value.strip()
        if re.fullmatch(r"\d{6}", value):
            value = "20" + value
        if re.fullmatch(r"\d{8}", value):
            value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


DATE_INPUTS = [{"type": "string", "format": "date"},
               {"type": "string", "pattern": r"^(?:[0-9]{6}|[0-9]{8})$"}]
Day = Annotated[date, BeforeValidator(parse_date), WithJsonSchema({"anyOf": DATE_INPUTS}, mode="validation")]
PaymentDay = Annotated[Day | None, WithJsonSchema(
    {"anyOf": [*DATE_INPUTS, {"type": "string", "const": ""}, {"type": "null"}]}, mode="validation")]

CommissionKey = Annotated[str, PathParam(description="本次操作的约稿 ID。使用新建约稿返回的 id，或从 GET /api/commissions 列表读取。")]
Queue = Literal["normal", "deferred"]
Status = Literal["未开始", "进行中", "已结稿"]


class Settings(BaseModel):
    """完整替换。每组去空白、删空项、保序去重后至少一项。省略使用内置默认值，首项作为以后保存的默认值。已有约稿不会被改写。"""
    contacts: list[str] = Field(
        default_factory=lambda: ["QQ", "微信", "B站", "Discord"]
    )
    platforms: list[str] = Field(default_factory=lambda: ["微信", "支付宝"])

    @field_validator("contacts", "platforms")
    @classmethod
    def distinct_names(cls, names):
        names = list(dict.fromkeys(n.strip() for n in names if n.strip()))
        if not names:
            raise ValueError("请至少保留一个选项")
        return names


class Payment(BaseModel):
    """一行稿费。没有行 ID。PUT 按提交顺序替换整个数组，包括重复行。"""
    amount: Annotated[Decimal, WithJsonSchema({"anyOf": [
        {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 10**12, "multipleOf": 0.01},
        {"type": "string", "pattern": r"^(?!0+(?:\.0{1,2})?$)[0-9]{1,12}(?:\.[0-9]{1,2})?$"},
    ]}, mode="validation")] = Field(
        gt=0, max_digits=14, decimal_places=2, description="人民币元，必须大于 0，整数部分最多 12 位，小数最多 2 位。建议传十进制字符串，如 1200.50，不加千位分隔符。规范给出推荐的十进制输入形式，服务兼容能精确解析的旧写法。响应金额为字符串，如 12.3、740.00，合计请用 Decimal。"
    )
    date: PaymentDay = Field(default_factory=today, description="null 或空字符串表示付款约定，省略字段按上海今天记为实收，PUT 和导入时也适用。")
    stage: str = Field("稿费", description="稿费阶段或用途，空白使用「稿费」")
    platform: str = Field("", description="留空使用设置中的首个收入平台")

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = super().__get_pydantic_json_schema__(core_schema, handler)
        if cls is Payment:
            schema["title"] = "PaymentInput" if handler.mode == "validation" else "PaymentOutput"
        return schema

    @field_validator("date", mode="before")
    @classmethod
    def allow_blank_date(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @model_serializer(mode="wrap")
    def serialize_payment(self, handler):
        data = handler(self)
        # Preserve explicit null even on routes that omit other optional fields.
        # Omitting this key on a later PUT would apply the legacy default (today).
        if self.date is None:
            data["date"] = None
        return data


class Attachment(BaseModel):
    id: str | None = Field(None, description="编辑时传回原附件 ID")
    name: str = "参考图"
    data: str | None = Field(
        None, description="PNG/JPEG/GIF/WebP 原图最多 12 MiB，支持 Base64 或 data URL。提供 data 时新建图片；普通写入忽略同时提交的 id。备份输出纯 Base64。"
    )


class Commission(BaseModel):
    nickname: str = Field("", description="空白时使用「甲方 」接 ID 前六字符大写，如甲方 AB12CD")
    contact_type: str = Field("", description="联系方式，如 QQ 或微信")
    contact: str = Field("", description="该联系方式对应的账号")
    title: str = Field("", description="约稿内容，在卡片上显示的简短需求标题")
    requirements: str = Field("", description="详细需求说明")
    is_priority: bool = Field(False, description="插队标记，插队新建时自动设置，移动、延期、恢复保留标记，可在编辑时更改")
    planned: Day | None = Field(None, description="预计开始日期，null 清空，空串 422；列表和详情中无日期时省略")
    started: Day | None = Field(None, description="实际开始日期，null 清空，空串 422；无日期时详情省略。开工时不会自动填写")
    status: Status = Field("未开始", description="结稿状态可选未开始、进行中、已结稿。已结稿的约稿进入历史列表。")
    queue: Queue = Field("normal", description="normal 表示排队中，deferred 表示延期等待。新建时默认 normal。")
    payments: list[Payment] = Field(default_factory=list, description="有序稿费行，无行 ID；PUT 覆盖整个数组，省略会清空。")
    attachments: list[Attachment] = Field(default_factory=list, description="PUT 保留的图片要传原附件 ID；省略列表会删除所有图片。")


class Saved(Commission):
    brief_received: bool = Field(False, json_schema_extra={"readOnly": True}, description="需求说明去除首尾空白后有内容，或至少有一张参考图时为 true")
    id: str = Field(description="约稿 ID，用作 /api/commissions/{key} 中的 key")
    sample: bool = Field(False, description="示例数据标记，由示例接口或备份恢复设置；示例参与统计。")


class ClientHistory(BaseModel):
    completed: int = Field(0, description="该甲方所有已结稿约稿数")
    total: Decimal = Field(Decimal(0), description="该甲方所有约稿截至上海今天的实收合计（元）")
    latest: date | None = Field(None, description="该甲方截至今天最近的实收日期，无实收时为空或省略")


class VersionedCommission(Saved):
    """已保存的约稿。id、brief_received、sample、revision 均由服务产生。"""
    sample: bool = Field(False, json_schema_extra={"readOnly": True}, description="是否为示例数据，示例计入统计")
    revision: str = Field(description="当前约稿正文的版本标记；PUT 在请求体中原样传回。仅移动顺序不会更新版本。")


class CommissionUpdate(Commission):
    """整单保存。省略 title、is_priority、queue 沿用现值，其余可写字段按端点说明重置。"""
    title: str = Field(default_factory=str, description="省略保留原约稿标题；显式空字符串清空")
    is_priority: bool = Field(default_factory=bool, description="省略保留原插队标记")
    queue: Queue = Field(default_factory=lambda: "normal", description="省略沿用当前队列，显式 normal/deferred 指定队列；另见预计开始日期改晚规则。")

    revision: str = Field(description="读取约稿时返回的版本。过期时返回 409。")


class Card(VersionedCommission):
    history: ClientHistory = Field(json_schema_extra={"readOnly": True}, description="有账号时按联系方式与账号归集；无账号时按昵称归集，昵称仅对应一个已知账号时并入该账号。")

    @computed_field(description="本单未填写支付日期的金额合计，仅队列 Card 提供；详情可自行合计 payments 中 date 为 null 的行。")
    @property
    def agreed_total(self) -> Decimal:
        return sum((p.amount for p in self.payments if p.date is None), Decimal(0))


class Receipt(Payment):
    """有支付日期的一行稿费，含未来日期。commission_id 指向所属约稿，无独立收款 ID。"""
    date: Day = Field(description="已填写的支付日期，无日期的付款约定不会出现在此列表")
    title: str = Field("", description="所属约稿的约稿内容（简短标题）")
    commission_id: str = Field(description="这笔稿费所属约稿的 ID")
    nickname: str = Field(description="所属约稿的甲方昵称")
    is_priority: bool = Field(False, description="所属约稿是否带插队标记")
    sample: bool = Field(description="所属约稿是否为示例数据")


class PaymentRequest(BaseModel):
    token: str = Field(description="所属约稿的一次追加稿费操作使用的 Idempotency-Key", min_length=1, max_length=128, pattern=r"^[!-~]+$")
    digest: str = Field(description="服务端对规范化请求体计算的 SHA-256；还原时原样传回", pattern=r"^[a-f0-9]{64}$")


class BackupCommission(Saved):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    payment_requests: list[PaymentRequest] = Field(default_factory=list, description="已使用的幂等键及摘要，导入后仍可去重；旧备份可省略。")


class Snapshot(BaseModel):
    """仅支持 version=1。恢复请提交完整原始 JSON，保留约稿 ID、图片及幂等记录。"""
    version: Literal[1]
    settings: Settings
    commissions: list[BackupCommission] = Field(description="依照队列顺序排列，含历史记录及图片")


class Movement(BaseModel):
    before: str | None = Field(None, description="移至此约稿之前，null 表示队尾")


@contextmanager
def database(write=False, *, streaming=False):
    connection = sqlite3.connect(DB, timeout=15, check_same_thread=not streaming)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield connection
        connection.commit()
    finally:
        connection.close()


def normalize_attachment_refs(db):
    affected = db.execute("""
        SELECT id,body FROM jobs WHERE EXISTS (
            SELECT 1 FROM json_each(jobs.body, '$.attachments')
            GROUP BY json_extract(value, '$.id') HAVING count(*) > 1
        )""").fetchall()
    for job in affected:
        body = json.loads(job["body"])
        seen, names, refs = set(), set(), []
        for ref in body["attachments"]:
            original = ref["id"]
            label = (original, ref["name"])
            if label in names:
                continue
            names.add(label)
            if original in seen:
                alias = uuid4().hex
                db.execute("""INSERT INTO images(id,job,mime,bytes,thumbnail)
                              SELECT ?,job,mime,bytes,thumbnail FROM images WHERE job=? AND id=?""",
                           (alias, job["id"], original))
                ref = {**ref, "id": alias}
            seen.add(original)
            refs.append(ref)
        body["attachments"] = refs
        db.execute("UPDATE jobs SET body=? WHERE id=?", (json.dumps(body, ensure_ascii=False), job["id"]))


@asynccontextmanager
async def lifespan(_):
    DB.parent.mkdir(parents=True, exist_ok=True)
    with database(True) as db:
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, pos INTEGER, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY, job TEXT REFERENCES jobs(id) ON DELETE CASCADE, mime TEXT, bytes BLOB);
            CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY, body TEXT NOT NULL);
        """)
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS payment_requests(
            job TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, token TEXT NOT NULL,
            digest TEXT NOT NULL, PRIMARY KEY(job,token))""")
        if "revision" not in {r["name"] for r in db.execute("PRAGMA table_info(jobs)")}:
            db.execute("ALTER TABLE jobs ADD COLUMN revision TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE jobs SET revision=lower(hex(randomblob(16)))")
        db.execute("""CREATE TRIGGER IF NOT EXISTS jobs_revision AFTER UPDATE OF body ON jobs
                      WHEN OLD.body != NEW.body BEGIN
                        UPDATE jobs SET revision=lower(hex(randomblob(16))) WHERE id=NEW.id;
                      END""")
        if "thumbnail" not in {
            r["name"] for r in db.execute("PRAGMA table_info(images)")
        }:
            db.execute("ALTER TABLE images ADD COLUMN thumbnail BLOB")
        db.execute(
            "INSERT OR IGNORE INTO settings VALUES(1,?)",
            (Settings().model_dump_json(),),
        )
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            if not db.execute("SELECT 1 FROM jobs LIMIT 1").fetchone():
                load_examples(db)
        if db.execute("PRAGMA user_version").fetchone()[0] < 2:
            normalize_attachment_refs(db)
            db.execute("PRAGMA user_version=2")
    yield


class ApiError(HTTPException):
    def __init__(self, status, code, detail, *, revision=None):
        super().__init__(status, detail)
        self.code = code
        self.revision = revision


class InvalidField(BaseModel):
    loc: list[str | int]
    msg: str
    type: str
    input: Any = None
    ctx: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    code: Literal["archived_commission", "before_target_archived", "before_target_not_found", "before_target_wrong_queue", "commission_not_found", "duplicate_attachment", "duplicate_payment_key", "idempotency_key_conflict", "image_not_found", "image_too_large", "invalid_attachment_reference", "invalid_backup_id", "invalid_image", "invalid_image_id", "invalid_transition", "origin_mismatch", "revision_conflict", "validation_error"]
    detail: str | list[InvalidField]
    current_revision: str | None = Field(None, description="仅 revision_conflict 返回最新版本")


def json_example(value):
    return {"content": {"application/json": {"example": value}}}


def header_spec(description):
    return {"description": description, "schema": {"type": "string"}}


ETAG_HEADER = {"ETag": header_spec('约稿版本，双引号中的值等于 revision。PUT 使用正文 revision 校验。')}


def version_headers(record):
    return {"ETag": '"' + record["revision"] + '"'}


def no_content(db, key):
    row = db.execute("SELECT revision FROM jobs WHERE id=?", (key,)).fetchone()
    return Response(status_code=204, headers=version_headers(row))


def write_errors(*, missing=False, conflict=None, images=False, validation=True):
    errors = {
        403: {"model": ErrorResponse, "description": "写请求的 Origin 与服务地址不一致"},
        422: {"model": ErrorResponse, "description": "参数校验失败或业务数据无效。按 detail 的字符串或数组形态读取。"},
    }
    if not validation:
        del errors[422]
    if missing:
        errors[404] = {"model": ErrorResponse, "description": "路径 key 对应的约稿不存在"}
    if conflict:
        errors[409] = {"model": ErrorResponse, "description": conflict}
    if images:
        errors[413] = {"model": ErrorResponse, "description": "单张原图上限 12 MiB，Base64 载荷超过 16,777,216 字符返回 413"}
    return errors


app = FastAPI(
    title="稿序 API",
    version="1.2.0",
    openapi_tags=[{"name": name, "description": desc} for name, desc in (
        ("约稿", "约稿正文、参考信息与甲方联想"), ("排队", "活动队列的状态与顺序"),
        ("收款", "登记稿费与查询收款明细"), ("附件", "原图和缩略图"), ("设置", "联系与支付方式"),
        ("统计", "按上海日期汇总收入"), ("工作台", "按页面读取一致快照"), ("数据", "备份、财务导出和示例"))],
    responses={500: {"description": "服务内部错误，响应可能为纯文本。核对写入结果后再决定是否重试。"}},
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
    servers=[{"url": "/"}],
    description="本规范描述当前 API；[调用指南](/llms.txt) 提供操作流程。info.version 是接口版本，备份格式 version 固定为 1。仅在可信网络或有访问控制的反向代理后部署。以 CNY 记账，日期使用上海时区。日期接受 YYYY-MM-DD、YYYYMMDD、YYMMDD（前缀 20，如 991231 表示 2099-12-31），输出为 YYYY-MM-DD。接口无需凭据，浏览器使用同源地址，服务不提供跨域 CORS。列表全量返回。\n\n" + GUIDE_SECTIONS["错误处理顺序"].strip(),
)


@app.exception_handler(ApiError)
async def api_error_response(request, error):
    payload = {"detail": error.detail, "code": error.code}
    if error.revision is not None:
        payload["current_revision"] = error.revision
    return JSONResponse(payload, status_code=error.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error_response(request, error):
    return JSONResponse({"code": "validation_error", "detail": jsonable_encoder(error.errors(), custom_encoder={bytes: lambda value: value.decode(errors="replace"), float: lambda value: value if math.isfinite(value) else str(value)})}, status_code=422)


@app.get("/healthz", include_in_schema=False)
def health():
    return {"status": "ok"}


@app.middleware("http")
async def check_request_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and origin
        and origin != str(request.base_url).rstrip("/")
    ):
        return JSONResponse({"detail": "来源与服务地址不一致", "code": "origin_mismatch"}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store"
        if response.status_code == 405:
            methods = {method for route in app.routes if route.matches(request.scope)[0] == Match.PARTIAL
                       for method in getattr(route, "methods", ())}
            if methods:
                response.headers["Allow"] = ", ".join(sorted(methods))
    return response


def options(db):
    return json.loads(db.execute("SELECT body FROM settings WHERE id=1").fetchone()[0])


def commission_body(raw):
    body = json.loads(raw)
    body["brief_received"] = bool(body.get("requirements", "").strip() or body.get("attachments"))
    return body


def dated_payments(record):
    return (p for p in record["payments"] if p.get("date"))


def rows(db):
    return [
        dict(commission_body(r["body"]), id=r["id"], revision=r["revision"])
        for r in db.execute("SELECT * FROM jobs ORDER BY pos, rowid")
    ]


def lookup(db, key):
    row = db.execute("SELECT body,revision FROM jobs WHERE id=?", (key,)).fetchone()
    if not row:
        raise ApiError(404, "commission_not_found", "约稿不存在")
    return dict(commission_body(row["body"]), id=key, revision=row["revision"])


def active_keys(db, queue):
    return [
        r[0]
        for r in db.execute(
            """SELECT id FROM jobs WHERE json_extract(body,'$.status') != '已结稿'
               AND COALESCE(json_extract(body,'$.queue'), 'normal') = ? ORDER BY pos, rowid""",
            (queue,),
        )
    ]


def reorder(db, keys):
    db.executemany("UPDATE jobs SET pos=? WHERE id=?", enumerate(keys))


def move(db, key, before, queue):
    keys = active_keys(db, queue)
    if key not in keys:
        raise ApiError(409, "archived_commission", "该约稿已结稿")
    if before is not None and before not in keys:
        target = db.execute("SELECT body FROM jobs WHERE id=?", (before,)).fetchone()
        code = "before_target_not_found"
        if target:
            code = "before_target_archived" if json.loads(target[0])["status"] == "已结稿" else "before_target_wrong_queue"
        raise ApiError(409, code, "队列已变化，请刷新")
    if key != before:
        keys.remove(key)
        keys.insert(len(keys) if before is None else keys.index(before), key)
        reorder(db, keys)


def transfer(db, key, queue):
    item = lookup(db, key)
    if item["status"] == "已结稿":
        raise ApiError(409, "archived_commission", "该约稿已结稿")
    if item.get("queue", "normal") != queue:
        db.execute(
            "UPDATE jobs SET body=json_set(body,'$.queue',?) WHERE id=?", (queue, key)
        )
        move(db, key, None, queue)


def thumbnail_bytes(content):
    with Image.open(BytesIO(content)) as picture:
        picture.thumbnail((240, 240))
        preview = ImageOps.exif_transpose(picture).convert("RGBA")
        output = BytesIO()
        preview.save(output, format="PNG", optimize=True)
        return output.getvalue()


def new_image(db, job, img, restore=False):
    try:
        encoded = img.data.split(",", 1)[-1]
        if len(encoded) > (MAX_IMAGE + 2) // 3 * 4:
            raise ApiError(413, "image_too_large", "单张图片最多 12 MB")
        content = base64.b64decode(encoded, validate=True)
        with Image.open(BytesIO(content)) as picture:
            mime = {
                "PNG": "image/png",
                "JPEG": "image/jpeg",
                "GIF": "image/gif",
                "WEBP": "image/webp",
            }.get(picture.format)
            if not mime or len(content) > MAX_IMAGE:
                raise ValueError()
            picture.verify()
        thumbnail = thumbnail_bytes(content)
    except (
        ValueError,
        binascii.Error,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        UnidentifiedImageError,
    ):
        raise ApiError(422, "invalid_image", "请选择有效的 PNG、JPEG、GIF 或 WebP 图片")
    key = img.id if restore and img.id else uuid4().hex
    if (
        not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key)
        or db.execute("SELECT 1 FROM images WHERE id=?", (key,)).fetchone()
    ):
        raise ApiError(422, "invalid_image_id", "备份中的附件 ID 无效或重复")
    db.execute(
        "INSERT INTO images(id,job,mime,bytes,thumbnail) VALUES(?,?,?,?,?)",
        (key, job, mime, content, thumbnail),
    )
    return key


def save(db, key, item, restore=False, sample=False):
    config = options(db)
    body = item.model_dump(mode="json", exclude={"attachments", "brief_received", "id", "revision", "payment_requests"})
    body["sample"] = sample
    body["nickname"] = item.nickname.strip() or f"甲方 {key[:6].upper()}"
    body["contact_type"] = item.contact_type or config["contacts"][0]
    for p in body["payments"]:
        p["platform"] = p["platform"] or config["platforms"][0]
        p["stage"] = p["stage"].strip() or "稿费"
    db.execute(
        "INSERT OR IGNORE INTO jobs(id,pos,body) SELECT ?, COALESCE(MAX(pos),-1)+1, '{}' FROM jobs",
        (key,),
    )
    existing = {r[0] for r in db.execute("SELECT id FROM images WHERE job=?", (key,))}
    refs = []
    seen = {}
    for img in item.attachments:
        if img.id and img.id in seen:
            original = seen[img.id]
            if not restore or not img.data or original.data != img.data:
                raise ApiError(422, "duplicate_attachment", "同一约稿包含重复的附件 ID")
            if img.name == original.name:
                continue
            # Preserve a differently named alias from a legacy snapshot as a distinct attachment.
            img = img.model_copy(update={"id": None})
        if img.id:
            seen[img.id] = img
        if img.data is not None:
            image_id = new_image(db, key, img, restore)
        elif img.id in existing:
            image_id = img.id
        else:
            raise ApiError(422, "invalid_attachment_reference", "图片附件不存在")
        refs.append({"id": image_id, "name": img.name})
    removed = existing - {r["id"] for r in refs}
    db.executemany("DELETE FROM images WHERE id=?", [(i,) for i in removed])
    body["attachments"] = refs
    db.execute(
        "UPDATE jobs SET body=? WHERE id=?", (json.dumps(body, ensure_ascii=False), key)
    )
    return lookup(db, key)


def histories(records):
    """Resolve known accounts first; join a nickname-only order if the match is unique."""
    accounts = defaultdict(set)
    identities = {}
    for r in records:
        name = r["nickname"].strip().casefold()
        contact = r["contact"].strip()
        if contact:
            key = (r["contact_type"], contact)
            accounts[name].add(key)
            identities[r["id"]] = key
    summaries = defaultdict(ClientHistory)
    cutoff = today()
    for r in records:
        name = r["nickname"].strip().casefold()
        known = accounts[name]
        key = identities.setdefault(
            r["id"], next(iter(known)) if len(known) == 1 else name
        )
        history = summaries[key]
        history.completed += int(r["status"] == "已结稿")
        for payment in dated_payments(r):
            day = date.fromisoformat(payment["date"])
            if day <= cutoff:
                history.total += Decimal(payment["amount"])
                history.latest = max(history.latest or day, day)
    return {key: summaries[identity] for key, identity in identities.items()}


@app.get(
    "/api/commissions",
    tags=["约稿"],
    summary="列出约稿及甲方历史，可按状态和队列筛选",
    response_model=list[Card],
    response_model_exclude_none=True,
    responses={200: json_example([api_examples.CARD]),
               422: {"model": ErrorResponse, "description": "路径或查询参数校验失败"}},
)
@documented('队列、筛选和顺位')
def list_commissions(status: Status | None = None, queue: Queue | None = None):
    """省略过滤条件时返回全部（含已结稿），按存储顺位、同位按插入顺序排列，可混合多个队列。传 queue 仅返回该队未结稿的约稿；查看历史用 status=已结稿 并省略 queue。"""
    with database() as db:
        records = rows(db)
    return commission_cards(records, status, queue)


def commission_cards(records, status=None, queue=None):
    history = histories(records)
    return [
        dict(x, history=history[x["id"]])
        for x in records
        if (status is None or x["status"] == status)
        and (
            queue is None
            or (x["status"] != "已结稿" and x.get("queue", "normal") == queue)
        )
    ]


@app.get(
    "/api/payments",
    tags=["收款"],
    summary="列出所有甲方的逐笔收款，按支付日期倒序",
    response_model=list[Receipt],
    responses={200: json_example([api_examples.RECEIPT])},
)
@documented('金额、日期与查询未收款')
def receipts():
    with database() as db:
        return receipt_rows(rows(db))


def receipt_rows(records):
    entries = [
        dict(
            p,
            commission_id=r["id"],
            title=r.get("title", ""),
            is_priority=r.get("is_priority", False),
            nickname=r["nickname"],
            sample=r.get("sample", False),
        )
        for r in records
        for p in dated_payments(r)
    ]
    return sorted(entries, key=lambda p: p["date"], reverse=True)


@app.post(
    "/api/commissions",
    tags=["约稿"],
    summary="新建约稿，可插入指定位置",
    status_code=201,
    response_model=VersionedCommission,
    response_model_exclude_none=True,
    responses={201: {"headers": {**ETAG_HEADER, "Location": header_spec("新单详情路径")}},
               **write_errors(images=True, conflict="before 目标不存在、已结稿或在其他队列")},
)
@documented('队列、筛选和顺位')
def create(
    response: Response,
    item: Annotated[Commission, Body(openapi_examples=api_examples.CREATE)],
    before: str | None = Query(None, description="位置填在 URL 查询参数中。传入同队未结稿约稿的 id，新单插在它前面；省略则追加到队尾；普通新建请留空。"),
):
    """请求体填写约稿信息。从 Examples 选择「填写联系账号、排期与稿费」可查看字段组合。
    `queue` 选 `normal`（排队中）或 `deferred`（延期等待）。直接建历史单请传 `status="已结稿"`。

    例如队列已有 A、B。调用 `POST /api/commissions?before=B的ID` 新建 C，结果为 A、C、B，
    C 自动带「插队」标记。只在请求体中设置 `is_priority=true` 会添加标记，位置仍按 before 决定。
    已结稿的新单忽略 before。成功返回 201，响应的 id 可用于后续操作。重复提交会创建多单。"""
    with database(True) as db:
        key = uuid4().hex
        insert_before = before if item.status != "已结稿" else None
        if insert_before:
            item.is_priority = True
        saved = save(db, key, item)
        if insert_before:
            move(db, key, insert_before, item.queue)
        response.headers.update(version_headers(saved))
        response.headers["Location"] = f"/api/commissions/{key}"
        return saved


@app.get(
    "/api/commissions/{key}",
    tags=["约稿"],
    summary="读取约稿详情",
    response_model=VersionedCommission,
    response_model_exclude_none=True,
    responses={200: {**json_example(api_examples.DETAIL), "headers": ETAG_HEADER}, 422: {"model": ErrorResponse, "description": "路径或查询参数校验失败"}, 404: {"model": ErrorResponse, "description": "约稿不存在"}},
)
@documented('版本和重试')
def detail(key: CommissionKey, response: Response):
    with database() as db:
        record = lookup(db, key)
        response.headers.update(version_headers(record))
        return record


@app.put(
    "/api/commissions/{key}",
    tags=["约稿"],
    summary="保存编辑后的约稿（与前端编辑窗口相同）",
    response_model=VersionedCommission,
    response_model_exclude_none=True,
    responses={200: {"headers": ETAG_HEADER}, **write_errors(missing=True, images=True, conflict="revision_conflict：重新 GET 并核对改动")},
)
@documented('整单编辑的空值规则', '版本和重试', '队列、筛选和顺位')
def update(key: CommissionKey, response: Response, item: Annotated[CommissionUpdate, Body(openapi_examples=api_examples.UPDATE)]):
    """先 GET 同一路径再编辑。

        未知字段会忽略。payments 没有行 ID：修改最新 GET 结果中的目标行，再提交整个数组。
    服务器直接按提交顺序保存；少传一行会删除该行，多传相同行会重复记账。
    同阶段同金额的行要在最新详情中按完整内容及位置核对；若无法唯一定位，请先确认。
    版本过期返回 409，此时重新读取并核对修改。

    与前端「编辑约稿 → 保存约稿」使用同一规则：编辑前后均未结稿，原队列和提交的队列均为 normal
    （编辑时省略 queue 表示沿用原队列），且新旧 planned 都有日期时，改到更晚一天或更晚会自动移入延期队尾。
    例如从 3 月 1 日改到 3 月 5 日会延期；首次填日期、清空、提前或保持日期都不会触发自动延期。
    前端卡片的「延期」按钮则直接调用 POST /api/commissions/{key}/defer，无需编辑日期。"""
    with database(True) as db:
        previous = lookup(db, key)
        if previous["revision"] != item.revision:
            raise ApiError(409, "revision_conflict", "约稿已被其他操作更新，请重新打开编辑窗口后再保存", revision=previous["revision"])
        if "title" not in item.model_fields_set:
            item.title = previous.get("title", "")
        if "is_priority" not in item.model_fields_set:
            item.is_priority = previous.get("is_priority", False)
        original = previous.get("queue", "normal")
        if "queue" not in item.model_fields_set:
            item.queue = original
        if (
            previous["status"] != "已结稿"
            and item.status != "已结稿"
            and original == item.queue == "normal"
            and previous["planned"]
            and item.planned
            and item.planned > date.fromisoformat(previous["planned"])
        ):
            item.queue = "deferred"
        saved = save(db, key, item, sample=previous.get("sample", False))
        if item.status != "已结稿" and (
            previous["status"] == "已结稿" or original != item.queue
        ):
            move(db, key, None, item.queue)
        response.headers.update(version_headers(saved))
        return saved


@app.delete(
    "/api/commissions/{key}", tags=["约稿"], summary="删除约稿、全部稿费、附件及本单幂等记录", status_code=204,
    responses=write_errors(missing=True),
)
def remove(key: CommissionKey):
    with database(True) as db:
        lookup(db, key)
        db.execute("DELETE FROM jobs WHERE id=?", (key,))
    return Response(status_code=204)


@app.post(
    "/api/commissions/{key}/move",
    tags=["排队"],
    summary="调整排队位置",
    status_code=204,
    responses={204: {"headers": ETAG_HEADER}, **write_errors(missing=True, conflict="当前单已结稿，或 before 目标不存在、已结稿或在其他队列")},
)
@documented('队列、筛选和顺位')
def move_job(key: CommissionKey, movement: Annotated[Movement, Body(openapi_examples=api_examples.MOVE)]):
    """路径 key 填要移动的约稿 ID，请求体 before 填目标约稿 ID。两单都须未结稿且在同一队列。
    例如队列为 A、B、C，向 /api/commissions/C的ID/move 发送 {"before":"A的ID"}，结果为 C、A、B。
    发送 {} 或 {"before":null} 会移到队尾。成功返回 204，跨队移动返回 409。"""
    with database(True) as db:
        row = lookup(db, key)
        move(db, key, movement.before, row.get("queue", "normal"))
        return no_content(db, key)


class StatusChange(BaseModel):
    status: Literal["进行中", "已结稿"]


@app.patch("/api/commissions/{key}/status", tags=["排队"], summary="开始约稿或结稿", status_code=204,
    responses={204: {"headers": ETAG_HEADER}, **write_errors(missing=True, conflict="当前状态不允许此次迁移，见下表")},
)
def change_status(key: CommissionKey, change: Annotated[StatusChange, Body(openapi_examples=api_examples.STATUS)]):
    """| 当前状态 | 请求进行中 | 请求已结稿 |
    |---|---|---|
    | 未开始 | 204 开始 | 409 |
    | 进行中 | 204 保持 | 204 结稿 |
    | 已结稿 | 409 | 204 保持 |

    未开始不在此请求枚举内，参数校验返回 422。此接口仅改 status，started 由编辑者填写。结稿后保留 queue 字段，仍可追加稿费。
    """
    with database(True) as db:
        current = lookup(db, key)["status"]
        expected = "未开始" if change.status == "进行中" else "进行中"
        if current not in (expected, change.status):
            raise ApiError(409, "invalid_transition", "约稿状态已变化，请刷新")
        if current == change.status:
            return no_content(db, key)
        db.execute(
            "UPDATE jobs SET body=json_set(body,'$.status',?) WHERE id=?",
            (change.status, key),
        )
        return no_content(db, key)


@app.post(
    "/api/commissions/{key}/defer",
    tags=["排队"],
    summary="移入延期等待队尾",
    status_code=204,
    responses={204: {"headers": ETAG_HEADER}, **write_errors(missing=True, conflict="已结稿约稿不能延期")},
)
@documented('队列、筛选和顺位')
def defer_job(key: CommissionKey):
    with database(True) as db:
        transfer(db, key, "deferred")
        return no_content(db, key)


@app.post(
    "/api/commissions/{key}/resume",
    tags=["排队"],
    summary="恢复至正常队尾",
    status_code=204,
    responses={204: {"headers": ETAG_HEADER}, **write_errors(missing=True, conflict="已结稿约稿不能恢复排队")},
)
@documented('队列、筛选和顺位')
def resume_job(key: CommissionKey):
    with database(True) as db:
        transfer(db, key, "normal")
        return no_content(db, key)


@app.post(
    "/api/commissions/{key}/payments",
    tags=["收款"],
    summary="追加一笔收款或无日期的付款约定",
    response_model=VersionedCommission,
    response_model_exclude_none=True,
    responses={200: {"headers": {**ETAG_HEADER, "Idempotent-Replayed": header_spec("首次写入 false；已处理的同内容重试 true")}},
               **write_errors(missing=True, conflict="idempotency_key_conflict：本单已用此键提交其他内容")},
)
@documented('幂等摘要', '版本和重试')
def pay(
    response: Response,
    key: CommissionKey,
    payment: Annotated[Payment, Body(openapi_examples=api_examples.PAYMENT)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[!-~]+$")],
):
    """键按约稿 ID 独立，限 1–128 个可打印 ASCII 字符（排除空格）。同键同体重试返回当前约稿和 200，Idempotent-Replayed 为 true；同键不同体返回 409。已结稿约稿也可追加。"""
    with database(True) as db:
        previous = lookup(db, key)
        # Omitted date/platform defaults may change between attempts.
        payload = payment.model_dump(mode="json", exclude_unset=True)
        payload["amount"] = str(payment.amount.normalize())
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        replay = db.execute("SELECT digest FROM payment_requests WHERE job=? AND token=?",
                            (key, idempotency_key)).fetchone()
        if replay:
            if replay["digest"] != digest:
                raise ApiError(409, "idempotency_key_conflict", "幂等键已用于其他收款内容")
            response.headers.update(version_headers(previous))
            response.headers["Idempotent-Replayed"] = "true"
            return previous
        db.execute("INSERT INTO payment_requests VALUES(?,?,?)", (key, idempotency_key, digest))
        item = Commission.model_validate(previous)
        item.payments.append(payment)
        saved = save(db, key, item, sample=previous.get("sample", False))
        response.headers.update(version_headers(saved))
        response.headers["Idempotent-Replayed"] = "false"
        return saved


@app.get(
    "/api/images/{key}",
    tags=["附件"],
    summary="获取原图或最多 240 像素的缩略图",
    response_class=Response,
    responses={422: {"model": ErrorResponse, "description": "路径或查询参数校验失败"},
        404: {"model": ErrorResponse, "description": "附件 ID 不存在"},
        200: {
            "content": {
                "image/png": {"schema": {"type": "string", "format": "binary"}},
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/webp": {"schema": {"type": "string", "format": "binary"}},
                "image/gif": {"schema": {"type": "string", "format": "binary"}},
            }
        }
    },
)
@documented('附件与备份边界')
def image(key: Annotated[str, PathParam(description="参考图的附件 ID，取自约稿详情 attachments[].id")], thumbnail: bool = Query(False, description="省略或 false 返回原图；true 返回等比、不放大的 PNG，长边最多 240px，大小可能超过原图")):
    with database() as db:
        if thumbnail:
            row = db.execute(
                "SELECT thumbnail FROM images WHERE id=?", (key,)
            ).fetchone()
            if row and row["thumbnail"] is not None:
                return Response(row["thumbnail"], media_type="image/png")
        row = db.execute("SELECT mime,bytes FROM images WHERE id=?", (key,)).fetchone()
        if not row:
            raise ApiError(404, "image_not_found", "图片不存在")
        if thumbnail:
            return Response(thumbnail_bytes(row["bytes"]), media_type="image/png")
        return Response(
            row["bytes"],
            media_type=row["mime"],
            headers={"X-Content-Type-Options": "nosniff"},
        )


@lru_cache(maxsize=2048)
def tokens(name):
    return [
        (c, p, p[:1]) for c, p in zip(name, lazy_pinyin(name, errors=lambda s: list(s)))
    ]


def matches(name, query):
    name, query = name.casefold(), query.casefold().replace(" ", "")
    if query in name:
        return True
    offsets = set()
    for variants in tokens(name):
        starts = offsets | {0}
        if any(v.startswith(query[i:]) for i in starts for v in variants):
            return True
        offsets = {
            i + len(v) for i in starts for v in variants if query.startswith(v, i)
        }
        if len(query) in offsets:
            return True
    return False


class Client(BaseModel):
    """甲方联想结果，提供昵称、联系方式和账号，无独立客户 ID。"""
    nickname: str
    contact: str
    contact_type: str


@app.get(
    "/api/clients",
    tags=["约稿"],
    summary="历史甲方联想，支持中文、拼音和首字母混输",
    response_model=list[Client],
    responses={200: json_example([{k: api_examples.DETAIL[k] for k in ("nickname", "contact_type", "contact")}]),
               422: {"model": ErrorResponse, "description": "路径或查询参数校验失败"}},
)
def suggest(q: str = Query("", openapi_examples={"nickname": api_examples.example("按昵称搜索", "小林"),
                                                "initials": api_examples.example("按拼音首字母搜索", "xl")}),
            contact_type: str | None = Query(None, description="指定联系方式时，按账号搜索并去重")):
    with database() as db:
        found = {}
        for r in db.execute("SELECT body FROM jobs ORDER BY rowid DESC"):
            x = json.loads(r[0])
            identity = (x["nickname"], x["contact_type"], x["contact"])
            if contact_type is not None:
                if x["contact_type"] != contact_type or not x["contact"] or q.strip().casefold() not in x["contact"].casefold():
                    continue
                identity = x["contact"]
            elif not matches(x["nickname"], q.strip()):
                continue
            if identity not in found:
                found[identity] = x
            if len(found) >= 8:
                break
        return list(found.values())


@app.get(
    "/api/settings",
    tags=["设置"],
    summary="读取联系方式和支付平台",
    responses={200: json_example(api_examples.OPTIONS)},
    response_model=Settings,
)
def settings():
    with database() as db:
        return options(db)


@app.put(
    "/api/settings",
    tags=["设置"],
    summary="更新可选列表，第一项为默认值",
    response_model=Settings,
    responses=write_errors(),
)
def set_options(item: Annotated[Settings, Body(openapi_examples=api_examples.SETTINGS)]):
    with database(True) as db:
        db.execute("UPDATE settings SET body=? WHERE id=1", (item.model_dump_json(),))
        return item


class PeriodIncome(BaseModel):
    total: float = Field(description="窗口内截至今天的实收额，元")
    average: float = Field(description="窗口实收额除以固定月数，含零收入月")


class Statistics(BaseModel):
    """含示例数据。统计使用 JSON 数字；月均用十进制取最近的分，平分时取偶。"""
    periods: dict[str, PeriodIncome] = Field(json_schema_extra={"required": ["3", "6", "12"], "propertyNames": {"enum": ["3", "6", "12"]}}, description="3、6、12 个自然月的收入与月均，含本月，零收入月参与平均")

    count: int = Field(description="正常排队且未结稿的约稿数")
    deferred_count: int = Field(description="延期等待队列中尚未结稿的约稿数量")
    until: date | None = Field(description="正常队列未结稿约稿中最晚的预计开始日，无日期时为空")
    total: float = Field(description="截至上海今天所有约稿累计实收，单位元")
    average: float = Field(description="累计实收除以首笔实收月至本月的自然月数，含零收入月")
    current: float = Field(description="上海本月截至今天的实收")
    years: list[int] = Field(description="有实收的年份，降序")
    monthly: dict[str, float] = Field(json_schema_extra={"propertyNames": {"pattern": r"^[0-9]{4}-(0[1-9]|1[0-2])$"}}, description="按 YYYY-MM 汇总，零收入月份补零；year 优先于 all_time 再优先于 months，仅影响本字段")
    platforms: dict[str, float] = Field(description="各支付平台累计实收，单位元")


@app.get(
    "/api/stats",
    tags=["统计"],
    summary="队列与收入统计，按支付日期归集",
    response_model=Statistics,
    responses={200: json_example(api_examples.STATS),
               422: {"model": ErrorResponse, "description": "路径或查询参数校验失败"}},
)
@documented('收入统计与甲方历史')
def stats(
    months: int = Query(6, ge=1, le=120, description="仅改变 monthly，含上海本月"),
    all_time: bool = Query(False, description="仅改变 monthly，从首笔实收所在月至本月"),
    year: int | None = Query(None, ge=1, le=9999, description="仅改变 monthly，以自然年覆盖 months 和 all_time"),
):
    """所有金额以元计，统计至上海今天，示例数据参与合计。月份筛选只改变 monthly，优先级为 year、all_time、months。历史年显示全年，本年截至本月，未来年为空。"""
    with database() as db:
        return income_stats(rows(db), months, all_time, year)


def income_stats(records, months, all_time, year):
    now = today()
    current = now.year * 12 + now.month - 1
    totals, sources = defaultdict(Decimal), defaultdict(Decimal)
    active = [
        r
        for r in records
        if r["status"] != "已结稿" and r.get("queue", "normal") == "normal"
    ]
    deferred_count = sum(r.get("queue") == "deferred" and r["status"] != "已结稿" for r in records)
    for r in records:
        for p in dated_payments(r):
            day = date.fromisoformat(p["date"])
            if day <= now:
                value = Decimal(p["amount"])
                totals[day.year * 12 + day.month - 1] += value
                sources[p["platform"]] += value
    total = sum(totals.values(), Decimal(0))
    span = current - min(totals, default=current) + 1
    years = sorted({m // 12 for m in totals}, reverse=True)
    first = year * 12 if year else current - months + 1
    if all_time and year is None:
        first = min(totals, default=current)
    last = min(first + 11, current) if year else current
    periods = {}
    for length in (3, 6, 12):
        subtotal = sum((totals.get(m, 0) for m in range(current - length + 1, current + 1)), Decimal(0))
        periods[str(length)] = {"total": subtotal, "average": round(subtotal / length, 2)}
    return {
        "periods": periods,
        "count": len(active),
        "deferred_count": deferred_count,
        "until": max((r["planned"] for r in active if r["planned"]), default=None),
        "total": total,
        "average": round(total / span, 2),
        "current": totals[current],
        "years": years,
        "monthly": {
            f"{m // 12:04d}-{m % 12 + 1:02d}": totals[m] for m in range(first, last + 1)
        },
        "platforms": dict(sorted(sources.items(), key=lambda x: -x[1])),
    }


class WorkspacePage(BaseModel):
    page: Literal["overview", "queue", "receipts", "settings"]
    settings: Settings
    count: int = Field(description="所有页面均为正常队列未结稿数")
    has_samples: bool = Field(description="库中是否含示例约稿")
    records: list = Field(max_length=0, description="此页无约稿列表，恒为 []")
    receipts: list[Receipt] = Field(max_length=0, description="此页无收款列表，恒为 []")


class OverviewStatistics(Statistics):
    until: date | None = Field(None, description="正常活动单的最晚预计开始日，无日期时省略")


class OverviewPage(WorkspacePage):
    page: Literal["overview"]
    records: list[VersionedCommission] = Field(description="普通前四单，再接延期前四单")
    stats: OverviewStatistics


class QueuePage(WorkspacePage):
    page: Literal["queue"]
    records: list[Card] = Field(description="全部队列及已结稿历史")


class ReceiptsPage(WorkspacePage):
    page: Literal["receipts"]
    receipts: list[Receipt]


class SettingsPage(WorkspacePage):
    page: Literal["settings"]


Workspace = Annotated[OverviewPage | QueuePage | ReceiptsPage | SettingsPage, Field(discriminator="page")]


@app.get("/api/workspace", tags=["工作台"], summary="单次读取事务中的当前页面快照",
         response_model=Workspace, response_model_exclude_none=True,
    responses={200: json_example(api_examples.WORKSPACE),
               422: {"model": ErrorResponse, "description": "路径或查询参数校验失败"}},
)
def workspace(page: Literal["overview", "queue", "receipts", "settings"],
              months: int = Query(6, ge=1, le=120, description="仅改变 monthly，含上海本月"), all_time: bool = False,
              year: int | None = Query(None, ge=1, le=9999)):
    """按 page 选择响应结构。所有页都返回 settings、count、has_samples。

    | page | records | 其他数据 |
    |---|---|---|
    | overview | VersionedCommission，普通前 4 单 + 延期前 4 单，均未结稿 | stats |
    | queue | Card，全部约稿含已结稿，附 history 和 agreed_total | |
    | receipts | 空数组 | receipts 全部有日期的稿费 |
    | settings | 空数组 | 配置 |

    非 receipts 页的 receipts 为空；非 overview 页省略 stats。"""
    with database() as db:
        result = {"page": page, "settings": options(db), "records": [], "receipts": []}
        if page == "settings":
            result["count"] = len(active_keys(db, "normal"))
            result["has_samples"] = bool(db.execute(
                "SELECT 1 FROM jobs WHERE json_extract(body,'$.sample')=1 LIMIT 1").fetchone())
            return result
        records = rows(db)
    normal = [r for r in records if r["status"] != "已结稿" and r.get("queue", "normal") == "normal"]
    result.update(count=len(normal), has_samples=any(r.get("sample") for r in records))
    if page == "overview":
        deferred = [r for r in records if r["status"] != "已结稿" and r.get("queue") == "deferred"]
        result.update(records=normal[:4] + deferred[:4], stats=income_stats(records, months, all_time, year))
    elif page == "queue":
        result["records"] = commission_cards(records)
    else:
        result["receipts"] = receipt_rows(records)
    return result


@app.get(
    "/api/export",
    tags=["数据"],
    summary="导出完整 JSON 备份（含图片）",
    response_model=Snapshot,
    responses={200: {**json_example({"version": 1, "settings": api_examples.OPTIONS, "commissions": []}),
                     "headers": {"Content-Disposition": header_spec("attachment，文件名 commission-YYYY-MM-DD.json")}}},
    response_model_exclude_none=True,
)
@documented('备份、示例与删除')
def export_data():
    def stream():
        with database(streaming=True) as db:
            yield '{"version":1,"settings":' + json.dumps(options(db)) + ',"commissions":['
            jobs = db.execute("SELECT id,body FROM jobs ORDER BY pos,rowid")
            first = True
            while batch := jobs.fetchmany(32):
                ids = [row["id"] for row in batch]
                slots = ",".join("?" for _ in ids)
                requests = defaultdict(list)
                for req in db.execute(f"SELECT job,token,digest FROM payment_requests WHERE job IN ({slots})", ids):
                    requests[req["job"]].append({"token": req["token"], "digest": req["digest"]})
                def image_data():
                    refs = [ref["id"] for row in batch for ref in json.loads(row["body"])["attachments"]]
                    for start in range(0, len(refs), 4):
                        keys = refs[start:start + 4]
                        params = ",".join("?" for _ in keys)
                        blobs = dict(db.execute(f"SELECT id,bytes FROM images WHERE id IN ({params})", keys))
                        for key in keys:
                            yield blobs[key]
                images = image_data()
                for row in batch:
                    record = commission_body(row["body"])
                    refs = record.pop("attachments")
                    record["id"] = row["id"]
                    if requests[row["id"]]:
                        record["payment_requests"] = requests[row["id"]]
                    if not first:
                        yield ","
                    first = False
                    yield json.dumps(record, ensure_ascii=False)[:-1] + ',"attachments":['
                    for i, ref in enumerate(refs):
                        if i:
                            yield ","
                        yield json.dumps(ref, ensure_ascii=False)[:-1] + ',"data":"'
                        data = next(images)
                        for start in range(0, len(data), 48 * 1024):
                            yield base64.b64encode(data[start:start + 48 * 1024])
                        yield '"}'
                    yield "]}"
            yield "]}"

    return StreamingResponse(stream(), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="commission-{today()}.json"' })


def download_text(content, extension, media_type):
    filename = f"commission-{today()}.{extension}"
    return Response(content, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{filename}"'
    })


@app.get("/api/export/csv", tags=["数据"], summary="下载约稿与收款 CSV，每笔收款一行，无收款约稿也保留一行",
         response_class=Response, responses={200: {"headers": {"Content-Disposition": header_spec("commission-YYYY-MM-DD.csv")},
             "content": {"text/csv; charset=utf-8": {"schema": {"type": "string"}}}}})
@documented('金额和财务导出')
def export_csv():
    with database() as db:
        records = rows(db)
    out = StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["约稿ID", "甲方", "联系方式", "联系账号", "约稿内容", "需求说明", "队列", "状态",
                     "预计开始", "实际开始", "支付日期", "稿期阶段", "支付方式", "金额(CNY)"])
    for r in records:
        for payment in list(dated_payments(r)) or [{}]:
            cells = [r["id"], r["nickname"], r["contact_type"], r["contact"], r.get("title", ""),
                     r["requirements"], r.get("queue", "normal"), r["status"], r["planned"], r["started"],
                     payment.get("date"), payment.get("stage"), payment.get("platform"), payment.get("amount")]
            # Preserve formula-like text as literal text when opened in a spreadsheet.
            writer.writerow("'" + c if isinstance(c, str) and c.lstrip().startswith(("=", "+", "-", "@"))
                            else c for c in cells)
    return download_text(out.getvalue().encode("utf-8-sig"), "csv", "text/csv")


def ledger_string(value):
    # Keep each Beancount string on its directive line, including user-entered notes.
    return json.dumps(" ".join(value.splitlines()), ensure_ascii=False)


@app.get("/api/export/beancount", tags=["数据"], summary="下载 CNY 双重记账账本，每笔收款对应一笔交易",
         response_class=PlainTextResponse, responses={200: {"headers": {"Content-Disposition": header_spec("commission-YYYY-MM-DD.beancount")}}})
@documented('金额和财务导出')
def export_beancount():
    entries = sorted(receipts(), key=lambda entry: entry["date"])
    first = entries[0]["date"] if entries else today().isoformat()
    lines = ['option "operating_currency" "CNY"', "",
             f"{first} open Assets:CommissionReceipts CNY", f"{first} open Income:Commissions CNY"]
    for entry in entries:
        amount = Decimal(entry["amount"])
        narration = " · ".join(filter(None, [entry["title"], entry["stage"]]))
        lines.extend(["", f'{entry["date"]} * {ledger_string(entry["nickname"])} {ledger_string(narration)}',
                      f'  commission_id: {ledger_string(entry["commission_id"])}',
                      f'  platform: {ledger_string(entry["platform"])}',
                      f"  Assets:CommissionReceipts  {amount:.2f} CNY",
                      f"  Income:Commissions  {-amount:.2f} CNY"])
    return download_text("\n".join(lines) + "\n", "beancount", "text/plain")


@app.post(
    "/api/import",
    tags=["数据"],
    summary="原子替换为完整备份，失败会回滚",
    status_code=204,
    responses=write_errors(images=True),
)
@documented('附件与备份边界', '幂等摘要')
def import_data(snapshot: Annotated[Snapshot, Body(openapi_examples=api_examples.IMPORT)]):
    """先用 GET /api/export 保存当前快照。无预演模式。导入在单个事务中替换设置、全部约稿、收款、图片及幂等记录。
    备份内约稿 id 必填且唯一（1–80 位字母、数字、下划线或短横线）。与现库 ID 相同也按备份重建；
    备份自身的重复约稿 ID 或同单重复 token 返回 422，修改回滚。"""
    ids = [r.id for r in snapshot.commissions]
    if len(ids) != len(set(ids)) or any(
        not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", i) for i in ids
    ):
        raise ApiError(422, "invalid_backup_id", "备份中的约稿 ID 无效或重复")
    with database(True) as db:
        db.execute("DELETE FROM jobs")
        db.execute(
            "UPDATE settings SET body=? WHERE id=1",
            (snapshot.settings.model_dump_json(),),
        )
        for item in snapshot.commissions:
            save(db, item.id, item, restore=True, sample=item.sample)
            if len({r.token for r in item.payment_requests}) != len(item.payment_requests):
                raise ApiError(422, "duplicate_payment_key", "备份包含重复的收款幂等键")
            db.executemany("INSERT INTO payment_requests VALUES(?,?,?)",
                           ((item.id, r.token, r.digest) for r in item.payment_requests))
    return Response(status_code=204)


def load_examples(db):
    if db.execute(
        "SELECT 1 FROM jobs WHERE json_extract(body,'$.sample')=1 LIMIT 1"
    ).fetchone():
        return
    now = today()
    for example in json.loads((ROOT / "examples.json").read_text(encoding="utf-8")):
        example["attachments"] = [
            {
                "name": name,
                "data": base64.b64encode(
                    (ROOT / "examples" / filename).read_bytes()
                ).decode(),
            }
            for name, filename in example.pop("reference_files", {}).items()
        ]
        for field in ("planned", "started"):
            if field in example:
                example[field] = now + timedelta(days=example[field])
        for payment in example.get("payments", []):
            payment["date"] = now + timedelta(days=payment["date"])
        save(db, uuid4().hex, Commission.model_validate(example), sample=True)


@app.post(
    "/api/examples",
    tags=["数据"],
    summary="添加示例约稿和收款，有示例时保持现状",
    status_code=204,
    responses=write_errors(validation=False),
)
def add_examples():
    with database(True) as db:
        load_examples(db)
    return Response(status_code=204)


@app.delete(
    "/api/examples",
    tags=["数据"],
    summary="仅清除标有示例的约稿及其收款",
    status_code=204,
    responses=write_errors(validation=False),
)
def remove_examples():
    with database(True) as db:
        db.execute("DELETE FROM jobs WHERE json_extract(body,'$.sample')=1")
    return Response(status_code=204)


@app.get("/llms.txt", include_in_schema=False)
def agent_guide():
    return FileResponse(ROOT / "llms.txt", media_type="text/plain")


@app.get("/docs", include_in_schema=False)
def docs():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="稿序 · API 文档",
        swagger_js_url="/docs-assets/swagger-ui-bundle.js",
        swagger_css_url="/docs-assets/swagger-ui.css",
        swagger_favicon_url="/static/logo.svg",
        swagger_ui_parameters={"defaultModelsExpandDepth": -1, "validatorUrl": None},
    )


app.mount(
    "/docs-assets",
    StaticFiles(directory=files("swagger_ui").joinpath("static")),
    name="docs-assets",
)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(ROOT / "static/index.html")
