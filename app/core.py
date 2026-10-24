"""Core URL validation, media classification, job runner for 拾光 Extract."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic", ".heif", ".mp4", ".mov", ".m4v", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
ALLOWED_DOMAINS = {
    "douyin.com": "抖音",
    "iesdouyin.com": "抖音",
    "tiktok.com": "TikTok",
    "xiaohongshu.com": "小红书",
    "xhslink.com": "小红书",
    "instagram.com": "Instagram",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "bilibili.com": "Bilibili",
    "b23.tv": "Bilibili",
    "weibo.com": "微博",
    "weibo.cn": "微博",
    "weibo.tv": "微博",
    "kuaishou.com": "快手",
    "gifshow.com": "快手",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "x.com": "X",
    "twitter.com": "X",
    "pinterest.com": "Pinterest",
    "pin.it": "Pinterest",
    "vimeo.com": "Vimeo",
    "threads.net": "Threads",
    "threads.com": "Threads",
}
MAX_TOTAL_BYTES = int(os.getenv("MAX_TOTAL_MB", "160")) * 1024 * 1024
MAX_ITEM_BYTES = int(os.getenv("MAX_ITEM_MB", "90")) * 1024 * 1024
MAX_ITEMS = 20
ROOT_DIR = Path(os.getenv("MEDIA_TMP_DIR", "/tmp/shiguang-media"))
ROOT_DIR.mkdir(parents=True, exist_ok=True)
JOB_TTL_SEC = 35 * 60
RUN_TIMEOUT_SEC = 105


class InputProblem(ValueError):
    pass


class MediaProblem(RuntimeError):
    pass


def normalize_share_url(text: str) -> tuple[str, str]:
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise InputProblem("请粘贴完整的作品分享链接。")
    matches = re.findall(r"https?://[^\s\u3000<>\"'，。；;！!）)】\]]+", text, flags=re.I)
    if not matches:
        raise InputProblem("没有检测到 http 或 https 链接。请复制作品的分享链接。")
    url = matches[0].rstrip(".,;!?。；！")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"https", "http"}:
        raise InputProblem("仅接受 http 或 https 链接。")
    if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise InputProblem("链接包含不允许的账户信息或端口。")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname or len(hostname) > 253 or ".." in hostname:
        raise InputProblem("链接域名不正确。")
    try:
        ipaddress.ip_address(hostname)
        raise InputProblem("不能使用 IP 地址链接，请使用平台的作品分享链接。")
    except ValueError as e:
        if isinstance(e, InputProblem):
            raise
    platform = next((name for domain, name in ALLOWED_DOMAINS.items()
                    if hostname == domain or hostname.endswith("." + domain)), None)
    if not platform:
        raise InputProblem("当前只支持页面列出的平台，暂不支持任意网站或网盘直链。")
    if not parsed.path or parsed.path == "/":
        if not (platform == "YouTube" and parsed.query.startswith("v=")):
            raise InputProblem("请使用单个作品的分享链接，不要使用平台首页。")
    lowpath = parsed.path.lower()
    # Never feed a website redirect endpoint to a media extraction tool.
    if re.search(r"/(?:redirect|out|go|jump|login|signup|settings|oauth|api)(?:/|$)", lowpath):
        raise InputProblem("请直接复制单个作品的分享链接，不要使用重定向或登录页面。")
    # Keep heavy profile, bulk feeds, and search pages out of a public free service.
    if re.search(r"/(?:search|playlist|channel|hashtag|tag|explore/feed|user/home)(?:/|$)", lowpath):
        raise InputProblem("暂不支持主页、合集或搜索页，请粘贴单个作品链接。")
    # remove fragment which is not necessary for server extraction
    clean = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))
    return clean, platform


def media_type(path: Path) -> str:
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        return "image"
    if path.suffix.lower() in VIDEO_EXTENSIONS:
        return "video"
    return "other"


def safe_filename(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name).strip(" .")
    return name[:120] or "media"


def file_size_total(base: Path) -> int:
    total = 0
    for path in base.rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def execute_limited(cmd: list[str], work_dir: Path, timeout: int = RUN_TIMEOUT_SEC) -> str:
    """Enforce time, output and cumulative filesystem bounds on extractor subprocess."""
    try:
        p = subprocess.Popen(cmd, cwd=work_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=(os.name != "nt"))
    except FileNotFoundError as ex:
        raise MediaProblem("服务端缺少媒体解析组件，请检查部署是否完成。") from ex
    result: dict[str, bytes] = {}

    def read_pipe():
        try:
            out, err = p.communicate()
            result["out"] = out[-450_000:]
            result["err"] = err[-6000:]
        except Exception:
            result["out"], result["err"] = b"", b""

    thread = threading.Thread(target=read_pipe, daemon=True)
    thread.start()
    start = time.monotonic()
    timed_out = False
    oversized = False
    while thread.is_alive():
        thread.join(timeout=0.30)
        if time.monotonic() - start > timeout:
            timed_out = True
            break
        if file_size_total(work_dir) > MAX_TOTAL_BYTES:
            oversized = True
            break
    if timed_out or oversized:
        try:
            if os.name != "nt":
                os.killpg(p.pid, signal.SIGKILL)
            else:
                p.kill()
        except (OSError, ProcessLookupError):
            pass
        thread.join(timeout=2)
        if timed_out:
            raise MediaProblem("解析超时。平台可能限制访问，请稍后换一个公开作品重试。")
        raise MediaProblem("媒体总大小超出本站的免费额度，请换一个较小的作品。")
    out = result.get("out", b"").decode("utf-8", "replace")
    err = result.get("err", b"").decode("utf-8", "replace")
    if p.returncode != 0:
        err_short = clean_error(err)
        raise MediaProblem(err_short)
    return out


def clean_error(stderr: str) -> str:
    low = stderr.lower()
    if "login" in low or "sign in" in low or "authentication" in low or "cookies" in low:
        return "平台要求登录验证，本站不会收集账号密码；请换公开可访问的作品。"
    if "private" in low or "unavailable" in low or "not available" in low:
        return "此作品可能不公开、已删除或当前地区不可访问。"
    if "unsupported url" in low or "unsupported" in low or "no suitable extractor" in low:
        return "当前解析组件暂不支持这个作品链接。"
    if "429" in low or "403" in low or "blocked" in low or "forbidden" in low:
        return "平台暂时限制了服务器访问（403/429）；请稍后再试。"
    if "filesize" in low or "maximum" in low and "size" in low:
        return "作品文件超过本站允许的大小。"
    return "平台暂时无法提取这个作品，可能需要更新解析组件或作品仅对登录用户开放。"


def collect_media(folder: Path, mode: str = "auto") -> list[Path]:
    result: list[Path] = []
    for file in folder.rglob("*"):
        if (file.is_file() and not file.is_symlink() and file.suffix.lower() in MEDIA_EXTENSIONS
                and not file.name.startswith(".")):
            if mode == "photos" and media_type(file) != "image":
                continue
            if mode == "video" and media_type(file) != "video":
                continue
            result.append(file)
    return sorted(result, key=lambda p: str(p).lower())[:MAX_ITEMS]


def build_zip(files: list[Path], base: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for file in files:
            z.write(file, file.relative_to(base).as_posix())


def _youtube_download(url: str, workdir: Path):
    cmd = ["yt-dlp", "--ignore-config", "--no-playlist", "--playlist-end", "20",
           "--no-progress", "--restrict-filenames", "--no-mtime", "--no-part",
           "--socket-timeout", "14", "--retries", "1", "--fragment-retries", "1",
           "--max-filesize", f"{MAX_ITEM_BYTES}", "--merge-output-format", "mp4",
           "--format", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
           "--output", "%(id)s_%(autonumber)02d.%(ext)s", "--paths", str(workdir), url]
    return execute_limited(cmd, workdir)


def _gallery_download(url: str, workdir: Path):
    cmd = ["gallery-dl", "--config-ignore", "--no-mtime", "--no-part",
           "--range", "1-20", "--post-range", "1", "--http-timeout", "14",
           "--retries", "1", "--filesize-max", str(MAX_ITEM_BYTES),
           "--directory", str(workdir), url]
    return execute_limited(cmd, workdir)


def inspect_link(url: str, platform: str) -> dict:
    """Best-effort metadata; failure is not a blocker to trying an actual download."""
    if platform == '小红书':
        from .xiaohongshu import fetch_note
        try:
            note = fetch_note(url)
            return {**{k: note[k] for k in ('title', 'author')},
                    'thumbnail': note['images'][0], 'count': len(note['images']),
                    'platform': platform, 'duration': None, 'preview_available': True}
        except MediaProblem as exc:
            return {'title': '已识别小红书链接', 'author': '', 'thumbnail': '',
                    'count': None, 'platform': platform, 'duration': None,
                    'preview_available': False, 'message': str(exc)}
    workdir = ROOT_DIR / ("inspect-" + uuid.uuid4().hex)
    workdir.mkdir(parents=True, exist_ok=False)
    try:
        out = execute_limited(["yt-dlp", "--ignore-config", "--no-playlist", "--playlist-end", "20",
                              "--skip-download", "--dump-single-json", "--socket-timeout", "10",
                              "--retries", "1", "--no-warnings", url], workdir, timeout=23)
        info = json.loads(out)
        entries = info.get("entries") or []
        if entries:
            valid = [e for e in entries if isinstance(e, dict)]
            item = valid[0] if valid else info
            count = min(len(entries), MAX_ITEMS)
        else:
            item = info
            count = 1
        thumb = item.get("thumbnail") or info.get("thumbnail") or ""
        if not isinstance(thumb, str) or not thumb.startswith(("http://", "https://")):
            thumb = ""
        return {
            "title": str(info.get("title") or item.get("title") or "作品已识别")[:120],
            "author": str(info.get("uploader") or info.get("channel") or info.get("creator") or "")[:70],
            "thumbnail": thumb,
            "duration": info.get("duration") if isinstance(info.get("duration"), (int, float)) else None,
            "count": count,
            "platform": platform,
            "preview_available": True,
        }
    except (MediaProblem, ValueError, json.JSONDecodeError, KeyError):
        return {"title": "已识别分享链接", "author": "", "thumbnail": "", "duration": None,
                "count": None, "platform": platform, "preview_available": False}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@dataclass
class Job:
    id: str
    created: float
    directory: Path
    status: str = "queued"
    message: str = "正在排队…"
    mode: str = "auto"
    platform: str = ""
    files: list[Path] = field(default_factory=list)
    archive: Path | None = None


class JobStore:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        self.semaphore = threading.BoundedSemaphore(2)

    def cleanup(self):
        with self.lock:
            old = [id for id, job in self.jobs.items()
                   if time.monotonic() - job.created > JOB_TTL_SEC and job.status != "working"]
            for id in old:
                job = self.jobs.pop(id)
                shutil.rmtree(job.directory, ignore_errors=True)

    def create(self, mode="auto", platform="") -> Job:
        self.cleanup()
        with self.lock:
            if len([j for j in self.jobs.values() if j.status in ("working", "queued")]) >= 5:
                raise MediaProblem("当前任务较多，请稍后再试。")
            id = uuid.uuid4().hex
            folder = ROOT_DIR / id
            folder.mkdir(parents=True, exist_ok=False)
            job = Job(id, time.monotonic(), folder, mode=mode, platform=platform)
            self.jobs[id] = job
            return job

    def get(self, id: str) -> Job | None:
        self.cleanup()
        with self.lock:
            return self.jobs.get(id)

    def schedule(self, job: Job, url: str, note: dict | None = None):
        threading.Thread(target=self.process, args=(job, url, note), daemon=True).start()

    def process(self, job: Job, url: str, note: dict | None = None):
        with self.semaphore:
            job.status = "working"
            job.message = "正在连接平台并获取媒体…"
            dl_dir = job.directory / "media"
            dl_dir.mkdir(exist_ok=True)
            if job.mode in ("photos", "live") or (job.mode == "auto" and job.platform in ("小红书", "Instagram", "Pinterest", "Threads")):
                strategies = [_gallery_download, _youtube_download]
            else:
                strategies = [_youtube_download, _gallery_download]
            if job.platform == '小红书' and job.mode != 'video':
                from .xiaohongshu import download_note, download_images
                strategies.insert(0, download_note)
                if note is not None:
                    strategies = [lambda _url, folder: download_images(note, folder)]
            errors: list[str] = []
            selected: list[Path] = []
            for downloader in strategies:
                try:
                    # Wipe incomplete files before trying the second extractor.
                    for p in dl_dir.iterdir():
                        if p.is_dir():
                            shutil.rmtree(p, ignore_errors=True)
                        else:
                            p.unlink(missing_ok=True)
                    job.message = "正在解析并下载公开媒体…"
                    downloader(url, dl_dir)
                    # For Live Photo, do not drop HEIC or MOV.
                    media_filter = job.mode if job.mode in ("video", "photos") else "auto"
                    selected = collect_media(dl_dir, media_filter)
                    if selected:
                        break
                    errors.append("当前工具未识别到所选类型的媒体文件。")
                except (MediaProblem, OSError) as e:
                    errors.append(str(e))
            if not selected:
                job.status = "error"
                job.message = (errors[0] if job.platform == '小红书' and job.mode != 'video'
                               else errors[-1]) if errors else "没有检测到可下载文件。"
                return
            if file_size_total(dl_dir) > MAX_TOTAL_BYTES:
                job.status = "error"
                job.message = "媒体总大小超过本站限制。"
                return
            job.files = selected
            job.message = "下载完成，可以保存文件。"
            try:
                if len(selected) > 1:
                    archive = job.directory / "拾光_作品素材.zip"
                    build_zip(selected, dl_dir, archive)
                    job.archive = archive
            except (OSError, zipfile.BadZipFile):
                job.message = "素材已下载，但打包失败；可以逐个保存。"
            job.status = "ready"


store = JobStore()
