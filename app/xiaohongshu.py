"""Read note images from public or user-saved pages, without executing scripts."""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .core import MAX_ITEM_BYTES, MAX_ITEMS, MAX_TOTAL_BYTES, MediaProblem, RUN_TIMEOUT_SEC

MAX_PAGE_BYTES = 5 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_url(url: str, domains: tuple[str, ...]):
    try:
        p = urlsplit(url)
        host = (p.hostname or '').lower()
        port = p.port
    except ValueError as exc:
        raise MediaProblem('图片地址格式不正确。') from exc
    if (p.scheme != 'https' or p.username or p.password or port not in (None, 443)
            or not any(host == d or host.endswith('.' + d) for d in domains)):
        raise MediaProblem('图片或页面地址不属于允许的小红书域名。')
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise MediaProblem('不允许访问内网地址。')
    except OSError as exc:
        raise MediaProblem('暂时无法连接小红书图片服务器。') from exc


def _open(url: str, domains: tuple[str, ...], timeout=14):
    _validate_url(url, domains)
    return build_opener(NoRedirect()).open(Request(url, headers={
        'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.xiaohongshu.com/',
        'Accept-Encoding': 'identity',
    }), timeout=timeout)


class StateParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.in_script = False
        self.scripts: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.in_script = tag == 'script' or self.in_script

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_script = False

    def handle_data(self, data):
        if self.in_script and '__INITIAL_STATE__' in data:
            self.scripts.append(data)


def parse_note(html: str, expected_id: str | None = None) -> dict:
    parser = StateParser()
    parser.feed(html)
    for script in parser.scripts:
        match = re.search(r'(?:window\.)?__INITIAL_STATE__\s*=\s*', script)
        if not match:
            continue
        # Replace JavaScript undefined only outside JSON strings. Never eval uploads.
        payload = re.sub(r'("(?:\\.|[^"\\])*"|\bundefined\b)',
                         lambda m: 'null' if m[0] == 'undefined' else m[0], script[match.end():])
        try:
            state, _ = json.JSONDecoder().raw_decode(payload.lstrip())
            details = state.get('note', {}).get('noteDetailMap', {})
            for key, entry in details.items():
                note = entry.get('note', {})
                note_id = note.get('noteId') or key
                if expected_id and note_id != expected_id:
                    continue
                images = []
                for item in note.get('imageList', [])[:MAX_ITEMS]:
                    variants = item.get('infoList') or []
                    preferred = next((v.get('url') for v in variants if v.get('imageScene') == 'WB_DFT'), None)
                    image = preferred or item.get('urlDefault') or item.get('url')
                    if isinstance(image, str) and image:
                        image = 'https:' + image if image.startswith('//') else image
                        if image.startswith('http://'):
                            image = 'https://' + image[7:]
                        # Keep the URL and transformation supplied by the platform unchanged.
                        images.append(image)
                if images:
                    return {'title': str(note.get('title') or '小红书图片笔记')[:120],
                            'author': str((note.get('user') or {}).get('nickname') or '')[:70],
                            'images': list(dict.fromkeys(images)), 'note_id': note_id}
        except (ValueError, TypeError, AttributeError):
            continue
    raise MediaProblem('页面中没有可读取的笔记图片。请在浏览器中打开具体笔记，确认图片已显示，再保存为 HTML 网页导入；登录页、截图和网页快捷方式不适用。')


def fetch_note(url: str) -> dict:
    for _ in range(5):
        try:
            with _open(url, ('xiaohongshu.com', 'xhslink.com')) as response:
                raw = response.read(MAX_PAGE_BYTES + 1)
                if len(raw) > MAX_PAGE_BYTES:
                    raise MediaProblem('作品页面过大，请使用保存网页导入。')
                match = re.search(r'/(?:explore|discovery/item)/([a-f0-9]+)', url)
                return parse_note(raw.decode('utf-8', 'replace'), match[1] if match else None)
        except HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                url = urljoin(url, exc.headers.get('Location', ''))
                if '/login' in urlsplit(url).path:
                    break
                continue
            raise MediaProblem('小红书暂时拒绝服务器访问，请尝试导入已保存的作品网页。') from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise MediaProblem('连接小红书超时，请稍后重试或导入作品网页。') from exc
    raise MediaProblem('小红书要求此访问登录验证。浏览器登录不会同步到本站服务器；请正常打开笔记并保存 HTML 网页后导入，无需上传密码或 Cookie。')


def download_images(note: dict, folder: Path):
    total = 0
    start = time.monotonic()
    types = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp',
             'image/gif': '.gif', 'image/avif': '.avif'}
    try:
        for index, url in enumerate(note['images'][:MAX_ITEMS], 1):
            remaining = RUN_TIMEOUT_SEC - (time.monotonic() - start)
            if remaining <= 0:
                raise MediaProblem('图片下载超时，请稍后重试。')
            with _open(url, ('xhscdn.com',), min(14, remaining)) as response:
                ext = types.get(response.headers.get_content_type())
                if not ext:
                    raise MediaProblem('图片地址没有返回有效的图片文件。')
                path = folder / f'photo_{index:02d}{ext}'
                size = 0
                with path.open('wb') as out:
                    while chunk := response.read(64 * 1024):
                        size += len(chunk)
                        total += len(chunk)
                        if size > MAX_ITEM_BYTES or total > MAX_TOTAL_BYTES:
                            raise MediaProblem('图片文件超过本站大小限制。')
                        if time.monotonic() - start > RUN_TIMEOUT_SEC:
                            raise MediaProblem('图片下载超时，请稍后重试。')
                        out.write(chunk)
                if size == 0:
                    raise MediaProblem('图片服务器返回了空文件。')
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise MediaProblem('图片下载失败，链接可能已过期，请重新打开笔记并保存网页后重试。') from exc


def download_note(url: str, folder: Path):
    download_images(fetch_note(url), folder)
