# 稿序 · CommissionManage

基于 FastAPI、SQLite 和原生 JavaScript 的约稿管理工作台。页面、图表以及交互式 API 文档的资源均随服务提供。

## 使用

- 在工作台或排队列表点击「新建约稿」。只记收款时，点击「添加稿费」填写金额和支付日期即可。昵称留空时自动编号，支付方式默认使用设置中的第一项。
- 通过拖动手柄排序，也可选中手柄后按上下方向键。点击两张卡片间的「插队」可指定新约稿的位置。点击「延期」或推迟正常队列中已有的预计开始日期，会将该约稿移至「延期等待」队尾。延期队列支持新建、插队、拖拽、编辑和结稿；点击「恢复排队」可回到正常队尾。结稿后可在「已结稿」中继续补填需求或收款。
- 日期支持日历选择、2026-02-28、20260228 和 260228。昵称支持中文、拼音和首字母混输，例如「小hmao」「x花m」均可匹配「小花猫」。
- 稿费按上海时区的支付日期汇总，未来日期的收款在当日计入。月均收入以首笔实收所在月至本月的自然月数计算，包含零收入月份。「预计排期至」取正常队列中最晚的预计开始日期。
- 「约稿内容」是卡片上的简短标题。点击展开完整需求和图片缩略图，点击缩略图可查看原图。旧记录可通过「查看约稿需求」展开。
- 甲方卡显示已完成单数、累计实收和最近收款日期。历史优先按联系方式和账号归集；未填账号时使用昵称，昵称对应多个账号时独立统计。收款记录页按日期倒序列出所有约稿的逐笔稿费，可搜索甲方、日期、阶段或支付方式，并打开原约稿。
- 首次启用此版本时，空库自动加入 9 条带标记的示例约稿和 8 笔收款。已有数据可在设置页手动加载示例，使用「清除示例」移除这些记录及关联数据。
- 设置页可维护支付方式及联系方式，下载或恢复含原图的 JSON 备份。导入前显示确认，导入失败会回滚。移除下拉选项不会改写历史记录。

## 本地运行

需要 Python 3.10 或更高版本。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 5444
```

打开 <http://127.0.0.1:5444/>。本地数据库默认为 `var/studio.sqlite3`，可通过 `COMMISSION_DB` 指定路径。

## Tailscale 服务器

从已加入对应 Tailnet、可执行 `tailscale ssh root@ubuntu-server-codex` 的仓库副本运行：

```sh
bash deploy.sh
```

脚本用 Git bundle 将当前分支已提交的版本传到服务器，在 `/home/code/CommissionManage` 建立或快进更新工作树，并连接 GitHub origin。首次部署私有仓库无需向服务器复制 GitHub 凭据。以后在服务器直接访问私有 origin 时，使用服务器自己的 GitHub 授权。

安装脚本创建 Python 虚拟环境并注册 systemd 开机服务，监听 <http://100.79.13.109:5444/>。服务的 SQLite 数据与原图保存在 `/var/lib/commission-manage/studio.sqlite3`，更新代码时保留。服务使用 Tailnet 访问控制，能连接该端口的成员可读写工作台。已有目标工作树可直接以 root 运行 `bash deploy/install.sh` 安装服务。

```sh
systemctl status commission-manage
journalctl -u commission-manage -n 80
```

## API

设置页可进入 `/docs` 调试接口，或从 `/openapi.json` 获取 OpenAPI 3.1 定义。新建、插队、移动、延期、收款、编辑、删除、附件读写、统计、甲方联想、设置和备份均可经 API 操作。

用金额和日期建立一条约稿并记账：

```sh
curl -X POST http://100.79.13.109:5444/api/commissions \
  -H 'Content-Type: application/json' \
  -d '{"payments":[{"amount":360,"date":"260228"}]}'
```

新建时可传 `?before=约稿ID`。完整更新使用 `PUT /api/commissions/{key}`，保留的图片传回原附件 ID，新增图片以 Base64 或 data URL 放入 `attachments[].data`。支持 PNG、JPEG、GIF 和 WebP，单张最多 12 MB。向已有约稿单独追加收款可使用 `POST /api/commissions/{key}/payments`。

队列字段 `queue` 可取 `normal` 或 `deferred`。查询 `GET /api/commissions?queue=deferred` 可获得延期队列及卡片的 `history`。使用 `POST /api/commissions/{key}/defer` 延期、`POST /api/commissions/{key}/resume` 恢复。`GET /api/payments` 返回全量收款明细。
