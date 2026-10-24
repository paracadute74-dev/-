# 拾光 Extract — 免费的公开媒体素材整理网站

**版本：1.1 · 手机优先 · 完整前端 + FastAPI 服务端**

### 1.1 图片提取改进

- 小红书图片笔记优先读取作品网页中的图片数据，保留顺序，并提供单张下载和 ZIP 打包。
- 若分享链接跳转登录页，会说明浏览器登录不会同步到 Render。分享链接中的 `xsec_token` 等参数会保留，不应手动删掉。
- 新增「小红书提示登录？导入已保存的作品网页」入口。电脑浏览器正常打开你有权保存的具体笔记，确认图片显示，Ctrl+S（Mac：⌘S）保存为 HTML，再导入。页面必须包含笔记图片数据；登录页、截图、快捷方式不适用。
- HTML 最大 5 MB，仅临时解析，不执行脚本、不持久化网页，不接收账号密码或 Cookie；只允许下载小红书图片 CDN 返回的图片，仍受大小和时间限制。图片链接过期或被 CDN 拒绝时，请重新保存网页重试。
- 网页导入功能以模拟笔记和图片响应测试完整下载流程，尚未用用户在登录浏览器中保存的真实作品网页验证。它不能保证绕过平台的访问限制，也不保证获得原图或 Live Photo。

> 粘贴单个公开作品链接，尝试获取视频、图集和实况素材；支持 iPhone 导出的 HEIC/JPG + MOV 原始文件配对打包。仅供保存自己拥有或获得许可的作品。

## ✅ 已包含功能

- 响应式中文页面，可在 iPhone / Android / Windows / macOS 浏览器使用。
- 支持粘贴带有中文文案的分享内容，自动识别其中的网址。
- 对抖音、小红书、TikTok、Instagram、Bilibili 等常见平台的**公开作品链接**进行尝试提取。
- 智能模式 / 视频模式 / 照片模式 / 实况素材模式；多文件打包 ZIP。
- 尽可能提供作品预览、提取状态、图片/视频预览与下载。
- 本地 Live Photo 配对：上传静态 HEIC/JPG 等文件与对应 MOV/MP4 视频，保存原始格式并打包。
- 独立服务端：网址白名单、限速、文件大小与时长限制、任务自动清理；不收集社交账号凭据。

**注意：**“能解析的平台”不等于“每个作品都可以下载”。平台经常更改接口并限制数据中心 IP；需要登录、私密、会员/付费、DRM、仅地区授权的作品不会被绕过。Live Photo 的站外链接也不保证同时提供原始照片+动态文件；本地 ZIP 只是保留原始组成文件，**不是重建苹果相册中的 Live Photo 对象**。

## 🚀 方法一：通过 Render 免费公开部署（推荐）

项目包含 `Dockerfile` 与 `render.yaml`，不需要自己购买服务器。准备：一个 GitHub 账号、一个 Render 账号、电脑浏览器。

### 第一步：上传代码到 GitHub

1. 下载并解压本项目压缩包。
2. 打开 https://github.com/new ，新建仓库，例如 `shiguang-extract`。选 Public 或 Private 均可；如选 Private，Render 需要被授权访问。
3. 打开新仓库，选择 **Add file → Upload files**。
4. 从电脑把**解压后文件夹里的内容**拖进上传区（可拖动整个目录/文件夹）；要保证 `Dockerfile`、`render.yaml`、`requirements.txt` 最后处于仓库根目录，且保留 `app/`、`static/` 目录结构。
5. 点击 **Commit changes** 完成上传。

GitHub 官方教程：https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository

### 第二步：创建免费的 Render Web Service

1. 打开 https://dashboard.render.com/ 注册并登录。
2. 点击 **New → Web Service**。
3. 连接 GitHub 账户，并选择刚创建的 `shiguang-extract` 仓库。
4. 配置：**Language = Docker**、**Instance Type = Free**。项目根目录已经有 Dockerfile，不需要额外填写 Python 启动指令。
5. 服务名称建议 `shiguang-extract-你的英文名`（保证不会重名）。
6. 点击 **Create Web Service**。等待 Render 自动构建，日志出现服务已启动后，打开页面分配给你的 HTTPS 网址。

也可在 Render 选择 **New → Blueprint** 并连接这个仓库，让 `render.yaml` 指定免费 Docker 服务。

**你最终的网址类似：** `https://shiguang-extract-xxx.onrender.com`（这是格式示例，不是已经发布的地址）。

### 第三步：手机实测

- iPhone Safari 打开 Render 分配的网址。
- 建议先测试 **Live Photo 配对**：手动选择一张 JPG 与一段 MOV；ZIP 下载在服务端本地打包，稳定性更容易验证。
- 然后粘贴一个完全公开、你有权下载的作品链接，测试视频/照片提取。平台限制可能导致失败。
- 打开 `https://你的网站地址/api/health` 应显示 `"status":"ok"`。

### Render 免费计划的限制

- 免费 Web Service **15 分钟没有访问会休眠**，再次访问时启动可能需要约 1 分钟。
- 每个 workspace 每月有一定免费实例小时数（官方文档目前为 750 小时），出站流量与构建分钟数也有限制。
- Render 在检测到异常大量对外抓取时可能暂停免费服务。
- 临时文件存放在容器磁盘，不是永久网盘；休眠、重启、部署时均可能丢失。本站任务约 **35 分钟自动过期**。
- 如果你的 Render 账户绑定了付款方式，**超出免费包含的流量/构建资源可能产生费用**。想严格控制成本，请检查 Render 的 Billing 与支出限制。
- Render 的公网 IP 属于数据中心：TikTok / Instagram / 小红书等平台可能返回 403、429 或要求登录。请勿将“网站部署成功”误解为“全网解析成功”。

参考官方文档：
- https://render.com/docs/free
- https://render.com/docs/web-services
- https://render.com/docs/docker

## 💻 方法二：在自己的电脑运行

可用于自测，无需发布公网：

1. 安装 Python 3.11+、FFmpeg（需要在系统 PATH 内）。
2. 在项目根目录依次执行：

```bash
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

3. 浏览器打开 `http://127.0.0.1:8000`。

或已安装 Docker Desktop 的情况下，在项目根目录执行：

```bash
docker build -t shiguang-extract .
docker run --rm -p 8000:10000 shiguang-extract
```

浏览器打开 `http://localhost:8000`。

## ⚙️ 自定义设置

可通过 Render 的 Environment 修改：

| 设置 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_TOTAL_MB` | `160` | 单任务媒体文件的最大总大小（MB） |
| `MAX_ITEM_MB` | `90` | 单媒体文件大小上限（MB） |
| `MEDIA_TMP_DIR` | `/tmp/shiguang-media` | 临时目录位置 |
| `PORT` | Render 自动分配 | 服务监听端口 |

后端单任务最多返回 20 项媒体文件，并限制执行时间。项目中的限制是为了让免费平台能承受个人轻量使用，未来提供公开高流量服务需要更完善的队列、鉴权、持久化存储、审计和 CDN。

## 🗂 项目结构

```
shiguang-extract/
├── app/
│   ├── main.py            # HTTP API、上传与下载
│   └── core.py            # 链接校验、媒体工具、后台任务
├── static/
│   ├── index.html         # 页面内容
│   ├── style.css          # 完整的手机/桌面响应式样式
│   └── app.js             # 按钮和解析状态交互
├── tests/test_app.py      # 测试
├── Dockerfile             # Render 一键容器部署
├── render.yaml            # 免费 Web Service 配置
└── requirements.txt       # 开源组件清单
```

## 常见问题

**网站能打开，但链接不能下载？** 可能是作品需要登录、被删除、平台反爬、数据中心 IP 被限制，或 yt-dlp / gallery-dl 暂时失效。请先尝试其他公开链接；定期更新依赖后重新部署。**不要上传账号密码或他人的 Cookie。**

**可以直接从 iPhone 相册提取 Live Photo 吗？** 网站不能直接读取苹果相册的私有实况数据。先从手机导出原始照片和配套 MOV（导出方式视 iOS 版本而定），再通过“Live Photo 配对”上传。

**为什么 ZIP 不是一张能动的苹果 Live Photo？** Live Photo 在苹果相册中还包含照片-视频的配对元数据。本站保留原始文件，不生成 Apple Photos 的原生资源记录。

**为什么不能全网提取？** 网站平台、权限、地区与法律限制各不相同，不存在可对所有平台持续有效的通用公开提取接口。本站不绕过付费或访问控制。

## 开源组件

- FastAPI：https://fastapi.tiangolo.com/
- yt-dlp：https://github.com/yt-dlp/yt-dlp
- gallery-dl：https://github.com/mikf/gallery-dl
- FFmpeg：https://ffmpeg.org/

网站原创源码使用 MIT License。开源依赖由其各自许可证约束。
