"""稿序 · 约稿队列与收入管理"""

import base64
import binascii
import hashlib
import ipaddress
import secrets
import csv
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
from typing import Annotated, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, BeforeValidator, Field, field_validator
from pypinyin import lazy_pinyin

ROOT = Path(__file__).parent
DB = Path(os.getenv("COMMISSION_DB", ROOT / "var" / "studio.sqlite3"))
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


Day = Annotated[date, BeforeValidator(parse_date)]
Queue = Literal["normal", "deferred"]
Status = Literal["未开始", "进行中", "已结稿"]


class Settings(BaseModel):
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
    amount: Decimal = Field(
        gt=0, max_digits=14, decimal_places=2, description="实收人民币，最多两位小数"
    )
    date: Day = Field(default_factory=today)
    stage: str = "稿费"
    platform: str = Field("", description="留空使用设置中的首个收入平台")


class Attachment(BaseModel):
    id: str | None = Field(None, description="编辑时传回原附件 ID")
    name: str = "参考图"
    data: str | None = Field(
        None, description="新图片或备份中的 Base64 编码，可含 data URL 前缀"
    )


class Commission(BaseModel):
    nickname: str = Field("", description="可留空，自动生成甲方编号")
    contact_type: str = ""
    contact: str = ""
    title: str = Field("", description="约稿内容，在卡片上显示的简短需求标题")
    requirements: str = ""
    brief_received: bool = False
    planned: Day | None = None
    started: Day | None = None
    status: Status = "未开始"
    queue: Queue = "normal"
    payments: list[Payment] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)


class Saved(Commission):
    id: str
    sample: bool = False


class ClientHistory(BaseModel):
    completed: int = 0
    total: Decimal = Decimal(0)
    latest: date | None = None


class VersionedCommission(Saved):
    revision: str


class CommissionUpdate(Commission):
    revision: str = Field(description="读取约稿时返回的版本。过期时返回 409。")


class Card(VersionedCommission):
    history: ClientHistory


class Receipt(Payment):
    title: str = Field("", description="所属约稿的约稿内容（简短标题）")
    commission_id: str
    nickname: str
    sample: bool


class PaymentRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128, pattern=r"^[!-~]+$")
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class BackupCommission(Saved):
    payment_requests: list[PaymentRequest] = Field(default_factory=list)


class Snapshot(BaseModel):
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


app = FastAPI(
    title="稿序 API",
    version="1.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
    description="智能体请先读 [API 调用指南](/llms.txt)，再按本 OpenAPI 定义构造请求。以 CNY 记账，日期使用上海时区。日期支持 YYYY-MM-DD、YYYYMMDD、YYMMDD。",
)


def local_request(request):
    if not request.client or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def access_role(request):
    admin = os.environ.get("COMMISSION_ADMIN_PASSWORD")
    viewer = os.environ.get("COMMISSION_VIEWER_PASSWORD")
    if not admin and not viewer and local_request(request):
        return "admin"
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "basic":
        try:
            username, _, password = base64.b64decode(token, validate=True).partition(b":")
        except (ValueError, binascii.Error):
            return None
        for login, secret in (("admin", admin), ("viewer", viewer)):
            if secret and username == login.encode() and secrets.compare_digest(password, secret.encode()):
                return login
    return None


@app.get("/healthz", include_in_schema=False)
def health():
    return {"status": "ok"}


@app.middleware("http")
async def authorize_request(request: Request, call_next):
    if request.url.path != "/healthz":
        role = access_role(request)
        if not role:
            return JSONResponse({"detail": "需要登录"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="CommissionManage", charset="UTF-8"'})
        if role != "admin" and (request.method not in ("GET", "HEAD", "OPTIONS")
                                or request.url.path.rstrip("/").startswith("/api/export")):
            return JSONResponse({"detail": "此操作需要管理员权限"}, status_code=403)
    origin = request.headers.get("origin")
    if (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and origin
        and origin != str(request.base_url).rstrip("/")
    ):
        return JSONResponse({"detail": "来源与服务地址不一致"}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store"
    return response


def options(db):
    return json.loads(db.execute("SELECT body FROM settings WHERE id=1").fetchone()[0])


def rows(db):
    return [
        dict(json.loads(r["body"]), id=r["id"], revision=r["revision"])
        for r in db.execute("SELECT * FROM jobs ORDER BY pos, rowid")
    ]


def lookup(db, key):
    row = db.execute("SELECT body,revision FROM jobs WHERE id=?", (key,)).fetchone()
    if not row:
        raise HTTPException(404, "约稿不存在")
    return dict(json.loads(row["body"]), id=key, revision=row["revision"])


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
    if key not in keys or (before is not None and before not in keys):
        raise HTTPException(409, "队列已变化，请刷新")
    if key != before:
        keys.remove(key)
        keys.insert(len(keys) if before is None else keys.index(before), key)
        reorder(db, keys)


def transfer(db, key, queue):
    item = lookup(db, key)
    if item["status"] == "已结稿":
        raise HTTPException(409, "该约稿已结稿")
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
            raise HTTPException(413, "单张图片最多 12 MB")
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
        raise HTTPException(422, "请选择有效的 PNG、JPEG、GIF 或 WebP 图片")
    key = img.id if restore and img.id else uuid4().hex
    if (
        not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key)
        or db.execute("SELECT 1 FROM images WHERE id=?", (key,)).fetchone()
    ):
        raise HTTPException(422, "备份中的附件 ID 无效或重复")
    db.execute(
        "INSERT INTO images(id,job,mime,bytes,thumbnail) VALUES(?,?,?,?,?)",
        (key, job, mime, content, thumbnail),
    )
    return key


def save(db, key, item, restore=False, sample=False):
    config = options(db)
    body = item.model_dump(mode="json", exclude={"attachments", "id", "revision", "payment_requests"})
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
                raise HTTPException(422, "同一约稿包含重复的附件 ID")
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
            raise HTTPException(422, "图片附件不存在")
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
        for payment in r["payments"]:
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
)
def list_commissions(status: Status | None = None, queue: Queue | None = None):
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
)
def receipts():
    with database() as db:
        return receipt_rows(rows(db))


def receipt_rows(records):
    entries = [
        dict(
            p,
            commission_id=r["id"],
            title=r.get("title", ""),
            nickname=r["nickname"],
            sample=r.get("sample", False),
        )
        for r in records
        for p in r["payments"]
    ]
    return sorted(entries, key=lambda p: p["date"], reverse=True)


@app.post(
    "/api/commissions",
    tags=["约稿"],
    summary="新建约稿，可插入指定位置",
    status_code=201,
    response_model=VersionedCommission,
    response_model_exclude_none=True,
)
def create(
    item: Annotated[Commission, Body(openapi_examples={
        "queue": {"summary": "新增排队约稿", "value": {"nickname": "小林", "title": "角色头像", "queue": "normal"}}
    })],
    before: str | None = None,
):
    """返回 201 和新约稿的 id。before 是同一队列中的约稿 ID，省略时排至队尾。重复提交会创建多条约稿。"""
    with database(True) as db:
        key = uuid4().hex
        saved = save(db, key, item)
        if before and item.status != "已结稿":
            move(db, key, before, item.queue)
        return saved


@app.get(
    "/api/commissions/{key}",
    tags=["约稿"],
    summary="读取约稿详情",
    response_model=VersionedCommission,
    response_model_exclude_none=True,
)
def detail(key: str):
    with database() as db:
        return lookup(db, key)


@app.put(
    "/api/commissions/{key}",
    tags=["约稿"],
    summary="完整更新约稿，推迟预计开始日期会进入延期队列",
    response_model=VersionedCommission,
    response_model_exclude_none=True,
)
def update(key: str, item: CommissionUpdate):
    """先 GET 详情再修改字段并回传。保留完整 payments 数组及 attachments 中已有的 id，省略这些列表会清空相应数据。状态变更和追加收款优先使用专用接口。"""
    with database(True) as db:
        previous = lookup(db, key)
        if previous["revision"] != item.revision:
            raise HTTPException(409, "约稿已被其他操作更新，请重新打开编辑窗口后再保存")
        if "title" not in item.model_fields_set:
            item.title = previous.get("title", "")
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
        return saved


@app.delete(
    "/api/commissions/{key}", tags=["约稿"], summary="删除约稿及其附件", status_code=204
)
def remove(key: str):
    with database(True) as db:
        lookup(db, key)
        db.execute("DELETE FROM jobs WHERE id=?", (key,))
    return Response(status_code=204)


@app.post(
    "/api/commissions/{key}/move",
    tags=["排队"],
    summary="调整排队位置",
    status_code=204,
)
def move_job(key: str, movement: Movement):
    with database(True) as db:
        row = lookup(db, key)
        move(db, key, movement.before, row.get("queue", "normal"))
    return Response(status_code=204)


class StatusChange(BaseModel):
    status: Literal["进行中", "已结稿"]


@app.patch("/api/commissions/{key}/status", tags=["排队"], summary="开始约稿或结稿", status_code=204)
def change_status(key: str, change: StatusChange):
    with database(True) as db:
        current = lookup(db, key)["status"]
        expected = "未开始" if change.status == "进行中" else "进行中"
        if current not in (expected, change.status):
            raise HTTPException(409, "约稿状态已变化，请刷新")
        db.execute(
            "UPDATE jobs SET body=json_set(body,'$.status',?) WHERE id=?",
            (change.status, key),
        )
    return Response(status_code=204)


@app.post(
    "/api/commissions/{key}/defer",
    tags=["排队"],
    summary="移入延期等待队尾",
    status_code=204,
)
def defer_job(key: str):
    with database(True) as db:
        transfer(db, key, "deferred")
    return Response(status_code=204)


@app.post(
    "/api/commissions/{key}/resume",
    tags=["排队"],
    summary="恢复至正常队尾",
    status_code=204,
)
def resume_job(key: str):
    with database(True) as db:
        transfer(db, key, "normal")
    return Response(status_code=204)


@app.post(
    "/api/commissions/{key}/payments",
    tags=["收款"],
    summary="追加一笔实收稿费",
    response_model=VersionedCommission,
    response_model_exclude_none=True,
)
def pay(
    key: str,
    payment: Annotated[Payment, Body(openapi_examples={
        "deposit": {"summary": "收取定金", "value": {"amount": "360.00", "stage": "定金", "date": "2026-02-28"}}
    })],
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128, pattern=r"^[!-~]+$")],
):
    """追加一笔人民币收款并返回更新后的约稿。重试时使用同一 Idempotency-Key 和请求体。"""
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
                raise HTTPException(409, "幂等键已用于其他收款内容")
            return previous
        db.execute("INSERT INTO payment_requests VALUES(?,?,?)", (key, idempotency_key, digest))
        item = Commission.model_validate(previous)
        item.payments.append(payment)
        return save(db, key, item, sample=previous.get("sample", False))


@app.get(
    "/api/images/{key}",
    tags=["附件"],
    summary="获取原图或最多 240 像素的缩略图",
    response_class=Response,
    responses={
        200: {
            "content": {
                "image/png": {},
                "image/jpeg": {},
                "image/webp": {},
                "image/gif": {},
            }
        }
    },
)
def image(key: str, thumbnail: bool = False):
    with database() as db:
        if thumbnail:
            row = db.execute(
                "SELECT thumbnail FROM images WHERE id=?", (key,)
            ).fetchone()
            if row and row["thumbnail"] is not None:
                return Response(row["thumbnail"], media_type="image/png")
        row = db.execute("SELECT mime,bytes FROM images WHERE id=?", (key,)).fetchone()
        if not row:
            raise HTTPException(404, "图片不存在")
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
    nickname: str
    contact: str
    contact_type: str


@app.get(
    "/api/clients",
    tags=["约稿"],
    summary="历史甲方联想，支持中文、拼音和首字母混输",
    response_model=list[Client],
)
def suggest(q: str = ""):
    with database() as db:
        found = {}
        for r in db.execute("SELECT body FROM jobs ORDER BY rowid DESC"):
            x = json.loads(r[0])
            identity = (x["nickname"], x["contact_type"], x["contact"])
            if identity not in found and matches(x["nickname"], q.strip()):
                found[identity] = x
            if len(found) >= 8:
                break
        return list(found.values())


@app.get(
    "/api/settings",
    tags=["设置"],
    summary="读取联系方式和支付平台",
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
)
def set_options(item: Settings):
    with database(True) as db:
        db.execute("UPDATE settings SET body=? WHERE id=1", (item.model_dump_json(),))
        return item


class PeriodIncome(BaseModel):
    total: float
    average: float


class Statistics(BaseModel):
    periods: dict[int, PeriodIncome] = Field(description="3、6、12 个自然月的收入与月均，含本月，零收入月参与平均")

    count: int
    deferred_count: int = Field(description="延期等待队列中尚未结稿的约稿数量")
    until: date | None
    total: float
    average: float
    current: float
    years: list[int]
    monthly: dict[str, float]
    platforms: dict[str, float]


@app.get(
    "/api/stats",
    tags=["统计"],
    summary="队列与收入统计，按支付日期归集",
    response_model=Statistics,
)
def stats(
    months: int = Query(6, ge=1, le=120),
    all_time: bool = Query(False, description="从首笔实收所在月起显示至本月"),
    year: int | None = Query(None, ge=1, le=9999, description="选择自然年查看历史收入"),
):
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
        for p in r["payments"]:
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
        periods[length] = {"total": subtotal, "average": round(subtotal / length, 2)}
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


class Workspace(BaseModel):
    page: Literal["overview", "queue", "receipts", "settings"]
    settings: Settings
    count: int
    has_samples: bool
    records: list[Card | VersionedCommission] = Field(default_factory=list)
    receipts: list[Receipt] = Field(default_factory=list)
    stats: Statistics | None = None


@app.get("/api/workspace", tags=["工作台"], summary="单次读取事务中的当前页面快照",
         response_model=Workspace, response_model_exclude_none=True)
def workspace(page: Literal["overview", "queue", "receipts", "settings"],
              months: int = Query(6, ge=1, le=120), all_time: bool = False,
              year: int | None = Query(None, ge=1, le=9999)):
    with database() as db:
        result = {"page": page, "settings": options(db)}
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
    response_model_exclude_none=True,
)
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
                    record = json.loads(row["body"])
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
         response_class=Response, responses={200: {"content": {"text/csv": {}}}})
def export_csv():
    with database() as db:
        records = rows(db)
    out = StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["约稿ID", "甲方", "联系方式", "联系账号", "约稿内容", "需求说明", "队列", "状态",
                     "预计开始", "实际开始", "支付日期", "稿期阶段", "支付方式", "金额(CNY)"])
    for r in records:
        for payment in r["payments"] or [{}]:
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
         response_class=PlainTextResponse)
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
)
def import_data(snapshot: Snapshot):
    ids = [r.id for r in snapshot.commissions]
    if len(ids) != len(set(ids)) or any(
        not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", i) for i in ids
    ):
        raise HTTPException(422, "备份中的约稿 ID 无效或重复")
    with database(True) as db:
        db.execute("DELETE FROM jobs")
        db.execute(
            "UPDATE settings SET body=? WHERE id=1",
            (snapshot.settings.model_dump_json(),),
        )
        for item in snapshot.commissions:
            save(db, item.id, item, restore=True, sample=item.sample)
            if len({r.token for r in item.payment_requests}) != len(item.payment_requests):
                raise HTTPException(422, "备份包含重复的收款幂等键")
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
