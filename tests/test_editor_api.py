import io
import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import editor, editor_routes
from app.editor_routes import _download_name, _stem, create_router
from app.tv import TVUnavailable


class FakeTV:
    def __init__(self):
        self.uploaded = []
        self.selected = []
        self.unavailable = False
        self.select_unavailable = False
        self.content_id = 'MY_F0001'

    def upload(self, jpeg):
        if self.unavailable:
            raise TVUnavailable('Could not reach the TV.')
        self.uploaded.append(jpeg)
        return self.content_id

    def select(self, content_id):
        if self.select_unavailable:
            raise TVUnavailable('Could not reach the TV.')
        self.selected.append(content_id)
        return True


@pytest.fixture
def tv():
    return FakeTV()


@pytest.fixture
def dirs(tmp_path):
    return tmp_path / 'uploads', tmp_path / 'processed'


@pytest.fixture
def client(tv, dirs):
    app = FastAPI()
    app.include_router(create_router(tv, *dirs))
    return TestClient(app)


def image_bytes(width, height, color='red', fmt='JPEG'):
    buf = io.BytesIO()
    Image.new('RGB', (width, height), color=color).save(buf, format=fmt)
    return buf.getvalue()


def upload(client, data, name='photo.jpg'):
    return client.post('/api/editor/upload', files={'file': (name, data)})


def process(client, filename, **extra):
    body = {'filename': filename, 'crop': {'x': 0, 'y': 0, 'width': 800, 'height': 450}, **extra}
    return client.post('/api/editor/process', json=body)


def processed(client):
    src = upload(client, image_bytes(800, 600)).json()
    return process(client, src['filename']).json()


def make_stale(path):
    two_days_ago = time.time() - 2 * 24 * 60 * 60
    os.utime(path, (two_days_ago, two_days_ago))


class TestUpload:
    def test_upload_success(self, client, dirs):
        resp = upload(client, image_bytes(800, 600), 'My Photo.jpg')
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['original_filename'] == 'My Photo.jpg'
        assert (data['width'], data['height']) == (800, 600)
        assert (dirs[0] / data['filename']).is_file()

    def test_upload_type_comes_from_content_not_name(self, client):
        resp = upload(client, image_bytes(100, 100, fmt='PNG'), 'misnamed.jpg')
        assert resp.json()['filename'].endswith('.png')

    def test_upload_reports_displayed_size_for_rotated_photo(self, client):
        buf = io.BytesIO()
        exif = Image.Exif()
        exif[0x0112] = 6
        Image.new('RGB', (400, 200)).save(buf, format='JPEG', exif=exif)
        data = upload(client, buf.getvalue()).json()
        assert (data['width'], data['height']) == (200, 400)

    def test_upload_not_an_image(self, client):
        resp = upload(client, b'this is not an image', 'notes.jpg')
        assert resp.status_code == 400
        assert resp.json()['detail'] == 'Invalid file type'

    def test_upload_unsupported_format(self, client):
        resp = upload(client, image_bytes(100, 100, fmt='GIF'), 'anim.gif')
        assert resp.status_code == 400

    def test_uploaded_source_is_served(self, client):
        original = image_bytes(800, 600)
        data = upload(client, original).json()
        resp = client.get(data['url'])
        assert resp.status_code == 200
        assert resp.content == original

    def test_upload_multi_picture_jpeg(self, client):
        # Phones and cameras write these as .jpg; Pillow calls the format MPO.
        buf = io.BytesIO()
        frames = [Image.new('RGB', (800, 600), color=c) for c in ('red', 'blue')]
        frames[0].save(buf, format='MPO', save_all=True, append_images=frames[1:])
        resp = upload(client, buf.getvalue())
        assert resp.status_code == 200
        assert resp.json()['filename'].endswith('.jpg')
        assert process(client, resp.json()['filename']).status_code == 200

    @pytest.mark.parametrize('fmt, extension', [('WEBP', '.webp'), ('BMP', '.bmp')])
    def test_upload_other_formats_can_be_processed(self, client, dirs, fmt, extension):
        src = upload(client, image_bytes(800, 600, fmt=fmt)).json()
        assert src['filename'].endswith(extension)
        data = process(client, src['filename']).json()
        with Image.open(dirs[1] / data['filename']) as img:
            assert img.size == (3840, 2160)

    def test_upload_truncated_image(self, client):
        data = image_bytes(800, 600)
        resp = upload(client, data[:len(data) // 2])
        assert resp.status_code == 400
        assert resp.json()['detail'] == 'Invalid file type'

    def test_upload_too_large(self, client, dirs, monkeypatch):
        monkeypatch.setattr(editor_routes, 'MAX_UPLOAD_BYTES', 100)
        resp = upload(client, image_bytes(800, 600))
        assert resp.status_code == 413
        assert list(dirs[0].iterdir()) == []

    @pytest.mark.parametrize('name', ['photo.jpg', '..%5C..%5Cconfig.toml', '0' * 32 + '.gif'])
    def test_source_rejects_names_it_did_not_issue(self, client, name):
        assert client.get(f'/api/editor/source/{name}').status_code == 404


class TestProcess:
    def test_process_success(self, client, dirs):
        src = upload(client, image_bytes(800, 600), 'Falls (1826).jpg').json()
        resp = process(client, src['filename'], original_filename=src['original_filename'])
        assert resp.status_code == 200
        data = resp.json()
        assert data['suggested_filename'] == 'Falls _1826__4k.jpg'
        with Image.open(dirs[1] / data['filename']) as img:
            assert img.size == (3840, 2160)
            assert img.format == 'JPEG'

    def test_process_fhd_preset(self, client, dirs):
        src = upload(client, image_bytes(800, 600)).json()
        data = process(client, src['filename'], preset='fhd').json()
        with Image.open(dirs[1] / data['filename']) as img:
            assert img.size == (1920, 1080)

    def test_process_letterbox_adds_bars(self, client, dirs):
        src = upload(client, image_bytes(400, 900, 'white')).json()
        body = {'filename': src['filename'], 'letterbox': True,
                'crop': {'x': 0, 'y': 0, 'width': 400, 'height': 900}}
        data = client.post('/api/editor/process', json=body).json()
        with Image.open(dirs[1] / data['filename']) as img:
            assert img.size == (3840, 2160)
            assert max(img.getpixel((10, 1080))) < 20
            assert min(img.getpixel((1920, 1080))) > 235

    def test_process_invalid_preset(self, client):
        src = upload(client, image_bytes(800, 600)).json()
        assert process(client, src['filename'], preset='8k').status_code == 400

    def test_process_unknown_file(self, client):
        assert process(client, '0' * 32 + '.jpg').status_code == 404

    def test_process_empty_crop_rejected(self, client):
        src = upload(client, image_bytes(800, 600)).json()
        body = {'filename': src['filename'], 'crop': {'x': 0, 'y': 0, 'width': 0, 'height': 0}}
        assert client.post('/api/editor/process', json=body).status_code == 422

    @pytest.mark.parametrize('name', ['../../config.toml', '..\\..\\config.toml', 'C:\\Windows\\win.ini', 'photo.jpg'])
    def test_process_rejects_names_it_did_not_issue(self, client, name):
        assert process(client, name).status_code == 404

    def test_process_crop_one_pixel_past_the_edge_is_not_padded(self, client, dirs):
        src = upload(client, image_bytes(1200, 800, 'white')).json()
        resp = process(client, src['filename'], crop={'x': 11, 'y': 0, 'width': 1190, 'height': 669})
        with Image.open(dirs[1] / resp.json()['filename']) as img:
            assert min(img.getpixel((3839, 1080))) > 235

    def test_process_oversized_crop_is_clamped_to_the_picture(self, client, dirs):
        src = upload(client, image_bytes(1200, 800, 'white')).json()
        resp = process(client, src['filename'], crop={'x': -500, 'y': -500, 'width': 1e9, 'height': 1e9})
        assert resp.status_code == 200
        with Image.open(dirs[1] / resp.json()['filename']) as img:
            assert min(img.getpixel((2, 2))) > 235
            assert min(img.getpixel((3837, 2157))) > 235

    def test_process_crop_outside_the_picture_rejected(self, client):
        src = upload(client, image_bytes(800, 600)).json()
        resp = process(client, src['filename'], crop={'x': 900, 'y': 700, 'width': 400, 'height': 225})
        assert resp.status_code == 400
        assert 'outside the picture' in resp.json()['detail']

    @pytest.mark.parametrize('letterbox', [False, True])
    def test_process_sub_pixel_crop_rejected(self, client, letterbox):
        src = upload(client, image_bytes(800, 600)).json()
        crop = {'x': 0, 'y': 0, 'width': 0.5, 'height': 0.5}
        assert process(client, src['filename'], crop=crop, letterbox=letterbox).status_code == 400

    def test_process_failure_does_not_leak_server_paths(self, client, dirs, monkeypatch):
        def fail(*args, **kwargs):
            raise OSError(2, 'No such file or directory', str(dirs[1] / 'result.jpg'))

        monkeypatch.setattr(editor, 'crop_and_upscale', fail)
        src = upload(client, image_bytes(800, 600)).json()
        resp = process(client, src['filename'])
        assert resp.status_code == 500
        assert resp.json()['detail'] == 'Processing failed.'


class TestDiptych:
    def body(self, client, **extra):
        a = upload(client, image_bytes(400, 900, 'red'), 'left.jpg').json()
        b = upload(client, image_bytes(500, 800, 'blue'), 'right.jpg').json()
        return {
            'filename1': a['filename'], 'filename2': b['filename'],
            'original_filename1': 'left.jpg', 'original_filename2': 'right.jpg',
            'crop1': {'x': 0, 'y': 0, 'width': 400, 'height': 900},
            'crop2': {'x': 0, 'y': 0, 'width': 500, 'height': 800},
            **extra,
        }

    def test_diptych_success(self, client, dirs):
        resp = client.post('/api/editor/process-diptych', json=self.body(client))
        assert resp.status_code == 200
        data = resp.json()
        assert data['suggested_filename'] == 'left_right_pair_4k.jpg'
        with Image.open(dirs[1] / data['filename']) as img:
            assert img.size == (3840, 2160)
            r, g, b = img.getpixel((100, 1080))
            assert r > 200 and b < 60
            r, g, b = img.getpixel((3700, 1080))
            assert b > 200 and r < 60

    def test_diptych_gap_is_clamped(self, client, dirs):
        data = client.post('/api/editor/process-diptych', json=self.body(client, gap_percent=50)).json()
        with Image.open(dirs[1] / data['filename']) as img:
            # Image 1 is 960px wide; a 10% gap ends at 960 + 384, a 50% gap would not.
            assert max(img.getpixel((960 + 200, 1080))) < 40
            assert img.getpixel((960 + 384 + 200, 1080))[2] > 200

    def test_diptych_gap_has_a_minimum(self, client, dirs):
        data = client.post('/api/editor/process-diptych', json=self.body(client, gap_percent=0)).json()
        with Image.open(dirs[1] / data['filename']) as img:
            # A 1% gap is 38px: black in the middle of it, image 2 just after.
            assert max(img.getpixel((960 + 19, 1080))) < 40
            assert img.getpixel((960 + 38 + 20, 1080))[2] > 200

    def test_diptych_no_room_for_second_image(self, client):
        body = self.body(client)
        body['crop1'] = {'x': 0, 'y': 0, 'width': 400, 'height': 200}
        assert client.post('/api/editor/process-diptych', json=body).status_code == 400

    def test_diptych_unknown_file(self, client):
        body = self.body(client)
        body['filename2'] = 'f' * 32 + '.jpg'
        assert client.post('/api/editor/process-diptych', json=body).status_code == 404


class TestResult:
    def test_result_is_shown_inline(self, client):
        resp = client.get(processed(client)['url'])
        assert resp.status_code == 200
        assert resp.headers['content-type'] == 'image/jpeg'
        assert 'attachment' not in resp.headers.get('content-disposition', '')

    def test_result_download_uses_custom_name(self, client):
        resp = client.get(processed(client)['download_url'] + '&name=Living%20room')
        assert 'attachment' in resp.headers['content-disposition']
        assert 'Living%20room.jpg' in resp.headers['content-disposition']

    def test_result_download_name_is_sanitised(self, client):
        resp = client.get(processed(client)['download_url'] + '&name=..%2F..%2Fevil')
        disposition = resp.headers['content-disposition']
        assert '/' not in disposition and '\\' not in disposition

    def test_result_not_found(self, client):
        assert client.get('/api/editor/result/' + '0' * 32 + '.jpg').status_code == 404


class TestSendToTV:
    def test_send_passes_processed_bytes_unchanged(self, client, tv, dirs):
        data = processed(client)
        resp = client.post(data['url'] + '/send', json={'show': False})
        assert resp.status_code == 200
        assert resp.json() == {'id': 'MY_F0001', 'selected': False, 'shown': False}
        assert tv.uploaded == [(dirs[1] / data['filename']).read_bytes()]
        assert tv.selected == []

    def test_send_and_show(self, client, tv):
        resp = client.post(processed(client)['url'] + '/send', json={'show': True})
        assert resp.json() == {'id': 'MY_F0001', 'selected': True, 'shown': True}
        assert tv.selected == ['MY_F0001']

    def test_send_without_body_does_not_show(self, client, tv):
        resp = client.post(processed(client)['url'] + '/send')
        assert resp.status_code == 200
        assert tv.selected == []

    def test_send_unknown_result(self, client, tv):
        resp = client.post('/api/editor/result/' + '0' * 32 + '.jpg/send', json={})
        assert resp.status_code == 404
        assert tv.uploaded == []

    def test_send_when_tv_unavailable(self, client, tv):
        tv.unavailable = True
        resp = client.post(processed(client)['url'] + '/send', json={})
        assert resp.status_code == 503
        assert resp.json()['detail'] == 'Could not reach the TV.'

    def test_send_succeeds_when_only_the_display_step_fails(self, client, tv):
        # The picture is on the TV by then; an error would invite a second, duplicate send.
        tv.select_unavailable = True
        resp = client.post(processed(client)['url'] + '/send', json={'show': True})
        assert resp.status_code == 200
        data = resp.json()
        assert (data['id'], data['selected'], data['shown']) == ('MY_F0001', False, False)
        assert 'could not be put on display' in data['warning']
        assert len(tv.uploaded) == 1

    def test_send_when_tv_does_not_confirm_the_upload(self, client, tv):
        tv.content_id = ''
        resp = client.post(processed(client)['url'] + '/send', json={'show': True})
        assert resp.status_code == 502
        assert tv.selected == []


class TestCleanup:
    def test_old_working_files_are_swept_on_upload(self, client, dirs):
        stale = dirs[0] / ('a' * 32 + '.jpg')
        stale.write_bytes(image_bytes(10, 10))
        two_days_ago = time.time() - 2 * 24 * 60 * 60
        os.utime(stale, (two_days_ago, two_days_ago))
        fresh = upload(client, image_bytes(800, 600)).json()
        assert not stale.exists()
        assert (dirs[0] / fresh['filename']).is_file()

    def test_old_results_are_swept_and_fresh_ones_kept(self, client, dirs):
        fresh = dirs[1] / processed(client)['filename']
        stale = dirs[1] / ('b' * 32 + '.jpg')
        stale.write_bytes(image_bytes(10, 10))
        make_stale(stale)
        upload(client, image_bytes(800, 600))
        assert not stale.exists()
        assert fresh.is_file()

    def test_upload_survives_a_file_that_cannot_be_deleted(self, client, dirs, monkeypatch):
        # On Windows a working file that is still open cannot be removed.
        stale = dirs[0] / ('c' * 32 + '.jpg')
        stale.write_bytes(image_bytes(10, 10))
        make_stale(stale)

        def locked(self, missing_ok=False):
            raise PermissionError(13, 'The process cannot access the file', str(self))

        monkeypatch.setattr(Path, 'unlink', locked)
        assert upload(client, image_bytes(800, 600)).status_code == 200

    def test_upload_recreates_deleted_working_folders(self, client, dirs):
        for folder in dirs:
            shutil.rmtree(folder)
        resp = upload(client, image_bytes(800, 600))
        assert resp.status_code == 200
        assert (dirs[0] / resp.json()['filename']).is_file()
        assert dirs[1].is_dir()


class TestNames:
    @pytest.mark.parametrize('original, expected', [
        ('Falls (1826).jpg', 'Falls _1826_'),
        ('C:\\scans\\plate.tar.gz', 'plate_tar'),
        ('../../etc/passwd', 'passwd'),
        ('', 'image'),
    ])
    def test_stem(self, original, expected):
        assert _stem(original) == expected

    @pytest.mark.parametrize('name, expected', [
        ('Living room', 'Living room.jpg'),
        ('x.JPG', 'x.JPG'),
        ('a/b', 'a_b.jpg'),
        ('....', 'fallback.jpg'),
        (None, 'fallback.jpg'),
    ])
    def test_download_name(self, name, expected):
        assert _download_name(name, 'fallback') == expected
