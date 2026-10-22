import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import InputProblem, build_zip, collect_media, media_type, normalize_share_url, safe_filename, store
from app.main import app

client = TestClient(app)


def test_share_url_text_with_chinese():
    url, name = normalize_share_url('我分享了一条小红书笔记 https://www.xiaohongshu.com/explore/abcd1234?foo=1 复制链接')
    assert name == '小红书'
    assert url == 'https://www.xiaohongshu.com/explore/abcd1234?foo=1'


@pytest.mark.parametrize('url', [
    'http://127.0.0.1:8000/private', 'file:///etc/passwd',
    'https://127.0.0.1:10000', 'https://attacker.com/video/1',
    'https://tiktok.com:8100/@test/video/10',
    'https://tiktok.com/', 'https://www.youtube.com/channel/xyz',
    'https://tiktok.com@127.0.0.1/video/1',
])
def test_reject_private_or_invalid_links(url):
    with pytest.raises(InputProblem):
        normalize_share_url(url)


def test_youtube_watch_link():
    url, name = normalize_share_url('https://www.youtube.com/watch?v=anything')
    assert 'watch?v=' in url and name == 'YouTube'


def test_health_and_homepage():
    assert client.get('/api/health').json()['status'] == 'ok'
    resp = client.get('/')
    assert resp.status_code == 200
    assert '拾光' in resp.text
    assert client.get('/static/style.css').status_code == 200


def test_unknown_domain_api():
    resp = client.post('/api/jobs', json={'url':'https://example.com/watch/1', 'mode':'auto'})
    assert resp.status_code == 400
    assert '只支持' in resp.json()['error']


def test_bad_mode_api():
    resp = client.post('/api/jobs', json={'url':'https://www.tiktok.com/@a/video/123', 'mode':'bad'})
    assert resp.status_code == 400


def test_zip_and_media_detect(tmp_path):
    (tmp_path / 'IMG_100.HEIC').write_bytes(b'heic-test')
    (tmp_path / 'IMG_100.MOV').write_bytes(b'mov-test')
    (tmp_path / 'ignored.txt').write_text('ignore')
    files = collect_media(tmp_path)
    assert len(files) == 2
    assert len(collect_media(tmp_path, mode='photos')) == 1
    assert {media_type(p) for p in files} == {'image','video'}
    pack = tmp_path / 'result.zip'
    build_zip(files, tmp_path, pack)
    with zipfile.ZipFile(pack) as z:
        assert set(z.namelist()) == {'IMG_100.HEIC', 'IMG_100.MOV'}


def test_live_upload_pair_and_zip():
    resp = client.post('/api/live-pair', files=[
        ('files', ('IMG_1111.HEIC', b'test-heic-content', 'image/heic')),
        ('files', ('IMG_1111.MOV', b'test-mov-content', 'video/quicktime')),
    ])
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data['status'] == 'ready'
    assert len(data['files']) == 2
    assert data['archive_url']
    download = client.get(data['archive_url'])
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as z:
        assert z.read('IMG_1111.HEIC') == b'test-heic-content'
        assert z.read('IMG_1111.MOV') == b'test-mov-content'
    one = client.get(data['files'][0]['download_url'])
    assert one.status_code == 200


def test_live_missing_pair_rejected():
    resp = client.post('/api/live-pair', files=[
        ('files', ('a.jpg', b'image1', 'image/jpeg')),
        ('files', ('b.jpg', b'image2', 'image/jpeg')),
    ])
    assert resp.status_code == 400
    assert '同时上传' in resp.json()['error']


def test_mocked_public_post_download_lifecycle(monkeypatch):
    import app.core as core
    def fake_download(url, folder):
        (folder / 'video_abc.mp4').write_bytes(b'mock-content')
        return ''
    monkeypatch.setattr(core, '_youtube_download', fake_download)
    resp = client.post('/api/jobs', json={'url':'https://www.tiktok.com/@demo/video/12345', 'mode':'video'})
    assert resp.status_code == 202
    id = resp.json()['id']
    for _ in range(50):
        result = client.get('/api/jobs/' + id).json()
        if result['status'] in ('ready', 'error'):
            break
        time.sleep(0.01)
    assert result['status'] == 'ready', result
    assert result['files'][0]['name'] == 'video_abc.mp4'
    file = client.get(result['files'][0]['download_url'])
    assert file.content == b'mock-content'
