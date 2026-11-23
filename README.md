# 稿序 · CommissionManage

基于 FastAPI、SQLite 和原生 JavaScript 的约稿管理工作台。页面、图表以及交互式 API 文档的资源均随服务提供。

## 使用

1. **新建约稿**　在工作台点击「新建约稿」，填写甲方昵称、约稿内容和预计开始日期，点击「保存约稿」。
2. **管理排队**　在「排队列表」拖动手柄调整顺序。点「插队」新建的约稿会带上同名标记，可在编辑时勾选或取消。开工时点「开始」，完成后点「结稿」，需要推迟时点「延期」。填写需求说明或添加参考图后，卡片会显示「已交需求」。
3. **登记收款**　编辑约稿，点击「添加稿费」，填写金额后保存。支付日期留空会记为付款约定，收到款项后补填日期即可转入收款记录。在「收款记录」点击任意一笔可查看对应约稿。
4. **查看收入**　在工作台查看收入图表。点击总收入或月均收入卡片可切换统计周期。
5. **备份数据**　在「设置」导出 JSON，之后可用它恢复数据。需要表格或账本时，选择 CSV 或 Beancount。

## Windows 桌面启动

安装 Python 3.10 或更高版本，包含 Windows Python 启动器（终端中可运行 `py -3 --version`）。将仓库下载或克隆到可写目录，双击根目录的 `start-windows.cmd`。首次启动需要联网下载依赖；脚本会创建项目虚拟环境，启动本机服务，并在默认浏览器打开 <http://127.0.0.1:5444/>。

脚本先使用 pip 当前配置的源，安装失败后自动改用清华大学 PyPI 镜像。如果网络较慢，可在项目目录直接运行以下命令，跳过首次尝试。镜像设置仅用于本次安装。

```bat
.\start-windows.cmd --cn-mirror
```

可为该脚本创建桌面快捷方式。使用期间保留命令窗口，按 Ctrl+C 停止服务。本机数据和原图保存在项目目录的 `var/studio.sqlite3`，也可通过环境变量 `COMMISSION_DB` 指定位置。

## 本地开发

在装有 Python 3.10 或更高版本的 Linux 或 macOS 环境中，从仓库根目录运行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 5444
```

打开 <http://127.0.0.1:5444/>。默认数据文件为 `var/studio.sqlite3`。

## Linux 部署

以下命令适用于使用 systemd 的 Ubuntu 22.04 或更新版本。安装脚本使用 Python 3.10+，将应用注册为开机服务，并把数据库和原图保存在 `/var/lib/commission-manage/studio.sqlite3`。

```sh
sudo apt-get update
sudo apt-get install -y git python3 python3-venv curl
sudo mkdir -p /home/code
sudo git clone https://github.com/Noeinmaxwell/CommissionManage.git /home/code/CommissionManage
cd /home/code/CommissionManage
sudo bash deploy/install.sh
```

默认监听本机 <http://127.0.0.1:5444/>。从自己的电脑访问远程部署时，可运行 `ssh -L 5444:127.0.0.1:5444 用户名@服务器`，再打开同一地址。要让可信网络中的其他设备直接访问，可将服务器的内网 IPv4 地址作为安装脚本的参数。能连接该地址的设备均可直接查看、编辑约稿和导出数据。

已有部署可在仓库目录执行 `sudo git pull --ff-only`，再用原监听地址重新运行安装脚本。使用以下命令查看运行状态和日志。

```sh
systemctl status commission-manage
journalctl -u commission-manage -n 80
```

## AI 使用声明

本项目使用 OpenAI Codex 辅助编写和修改代码、测试及文档。约稿管理与收入统计在本地服务中运行，无需配置 AI API 密钥。

## API

智能体可先读取 `/llms.txt` 获取调用顺序、请求示例、重试语义和错误说明。设置页可进入 `/docs` 调试接口，或从 `/openapi.json` 获取 OpenAPI 3.1 定义。新建、插队、移动、延期、收款、编辑、删除、附件读写、统计、甲方联想、设置和备份均可经 API 操作。

用金额和日期建立一条约稿并记账：

```sh
curl -X POST http://127.0.0.1:5444/api/commissions \
  -H 'Content-Type: application/json' \
  -d '{"payments":[{"amount":360,"date":"260228"}]}'
```

新建时可传 `?before=约稿ID`。完整更新使用 `PUT /api/commissions/{key}` 并传回读取时的 `revision`，保留的图片传回原附件 ID，新增图片以 Base64 或 data URL 放入 `attachments[].data`。支持 PNG、JPEG、GIF 和 WebP，单张最多 12 MB。为一次收款生成唯一的 `Idempotency-Key` 请求头，重试时复用。向已有约稿单独追加收款可使用 `POST /api/commissions/{key}/payments`。

队列字段 `queue` 可取 `normal` 或 `deferred`。查询 `GET /api/commissions?queue=deferred` 可获得延期队列及卡片的 `history`。使用 `POST /api/commissions/{key}/defer` 延期、`POST /api/commissions/{key}/resume` 恢复。`GET /api/payments` 返回全量收款明细。
