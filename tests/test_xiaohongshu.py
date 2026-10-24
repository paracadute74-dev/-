import io
import json
import time
from email.message import Message
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from app import xiaohongshu as xhs
from app.core import MediaProblem, JobStore
from app.main import app


def page(images=None, title='图片 undefined 标题'):
    note = {'noteId': 'abc123', 'title': title, 'user': {'nickname': '作者'},
            'imageList': images if images is not None else [
                {'urlDefault': 'https://sns-img.xhscdn.com/a'},
                {'urlDefault': 'https://sns-img.xhscdn.com/b'}]}
    return '<script>window.__INITIAL_STATE__=' + json.dumps(
        {'note': {'noteDetailMap': {'abc123': {'note': note}}}}, ensure_ascii=False
    )[:-1] + ',"unused":undefined}</script>'


def test_parse_saved_page_and_exact_note():
    note = xhs.parse_note(page(), 'abc123')
    assert note['title'] == '图片 undefined 标题'
    assert len(note['images']) == 2
    with pytest.raises(MediaProblem):
        xhs.parse_note(page(), 'another-note')


def test_platform_image_variants_keep_transform_and_use_https():
    note = xhs.parse_note(page([{'urlDefault': 'http://sns-img.xhscdn.com/a!webp',
        'infoList': [{'imageScene': 'WB_DFT', 'url': '//sns-img.xhscdn.com/b!webp'}]}]))
    assert note['images'] == ['https://sns-img.xhscdn.com/b!webp']


def test_reject_login_page_and_do_not_execute_javascript():
    for html in ['<h1>登录</h1>', '<script>window.__INITIAL_STATE__=alert(1)</script>']:
        with pytest.raises(MediaProblem):
            xhs.parse_note(html)


@pytest.mark.parametrize('url', ['http://sns-img.xhscdn.com/a',
    'https://xhscdn.com.attacker.test/a', 'https://127.0.0.1/a',
    'https://user:pass@sns-img.xhscdn.com/a', 'https://sns-img.xhscdn.com:444/a',
    'https://[broken/a', 'https://sns-img.xhscdn.com:invalid/a'])
def test_reject_untrusted_image_hosts(url):
    with pytest.raises(MediaProblem):
        xhs._validate_url(url, ('xhscdn.com',))


def test_reject_private_dns(monkeypatch):
    monkeypatch.setattr(xhs.socket, 'getaddrinfo', lambda *a, **k: [(0, 0, 0, '', ('127.0.0.1', 443))])
    with pytest.raises(MediaProblem):
        xhs._validate_url('https://sns-img.xhscdn.com/a', ('xhscdn.com',))


def test_login_redirect_has_actionable_message(monkeypatch):
    def redirect(*args, **kwargs):
        raise HTTPError(args[0], 302, 'redirect', {'Location': '/login'}, None)
    monkeypatch.setattr(xhs, '_open', redirect)
    with pytest.raises(MediaProblem, match='不会同步'):
        xhs.fetch_note('https://www.xiaohongshu.com/explore/abc123')


def test_import_download_lifecycle(monkeypatch):
    class ImageResponse(io.BytesIO):
        headers = Message()
        headers['Content-Type'] = 'image/jpeg'
    monkeypatch.setattr(xhs, '_open', lambda *a, **k: ImageResponse(b'jpeg-content'))
    client = TestClient(app)
    response = client.post('/api/import-page', files={'file': ('note.html', page().encode(), 'text/html')})
    assert response.status_code == 202, response.text
    job_id = response.json()['id']
    for _ in range(100):
        job = client.get('/api/jobs/' + job_id).json()
        if job['status'] in ('ready', 'error'):
            break
        time.sleep(.01)
    assert job['status'] == 'ready', job
    assert len(job['files']) == 2
    assert client.get(job['files'][0]['download_url']).content == b'jpeg-content'
    assert client.get(job['archive_url']).status_code == 200


def test_import_validation():
    client = TestClient(app)
    for name, raw, status in [('a.txt', b'hi', 400), ('a.html', b'<h1>login</h1>', 400),
                              ('a.html', b'x' * (xhs.MAX_PAGE_BYTES + 1), 413)]:
        response = client.post('/api/import-page', files={'file': (name, raw, 'text/html')})
        assert response.status_code == status


def test_images_only_preserve_order_and_skip_video(monkeypatch, tmp_path):
    from app import core
    def images(url, folder):
        (folder / 'photo_01.jpg').write_bytes(b'1')
        (folder / 'photo_02.jpg').write_bytes(b'2')
    monkeypatch.setattr(xhs, 'download_note', images)
    monkeypatch.setattr(core, '_youtube_download', lambda *a: pytest.fail('unexpected video extraction'))
    store = JobStore()
    job = store.create(mode='photos', platform='小红书')
    store.process(job, 'https://www.xiaohongshu.com/explore/abc123')
    assert job.status == 'ready'
    assert [f.name for f in job.files] == ['photo_01.jpg', 'photo_02.jpg']


def test_reject_oversized_image(monkeypatch, tmp_path):
    class ImageResponse(io.BytesIO):
        headers = Message()
        headers['Content-Type'] = 'image/jpeg'
    monkeypatch.setattr(xhs, '_open', lambda *a, **k: ImageResponse(b'too-large'))
    monkeypatch.setattr(xhs, 'MAX_ITEM_BYTES', 2)
    with pytest.raises(MediaProblem, match='大小限制'):
        xhs.download_images({'images': ['https://sns-img.xhscdn.com/a']}, tmp_path)
