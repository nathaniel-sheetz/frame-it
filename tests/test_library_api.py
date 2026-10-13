import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import editor_routes, library_routes
from app.library import Library
from app.tv import TVUnavailable


class FakeTV:
    def __init__(self):
        self.uploaded = []
        self.selected = []
        self.deleted = []
        self.unavailable = False
        self.art_mode = True
        self.confirms_delete = True

    def _reach(self):
        if self.unavailable:
            raise TVUnavailable('Could not reach the TV.')

    def upload(self, jpeg):
        self._reach()
        self.uploaded.append(jpeg)
        return f'MY_F{len(self.uploaded):04d}'

    def select(self, content_id):
        self._reach()
        self.selected.append(content_id)
        return self.art_mode

    def delete(self, content_id):
        self._reach()
        if self.confirms_delete:
            self.deleted.append(content_id)
        return self.confirms_delete


@pytest.fixture
def tv():
    return FakeTV()


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / 'library')


@pytest.fixture
def client(tv, library, tmp_path):
    app = FastAPI()
    app.include_router(editor_routes.create_router(tv, library, tmp_path / 'processed'))
    app.include_router(library_routes.create_router(tv, library))
    return TestClient(app)


def image_bytes(width, height, color='red'):
    buf = io.BytesIO()
    Image.new('RGB', (width, height), color=color).save(buf, format='JPEG')
    return buf.getvalue()


def original(client, width=3840, height=2160, color='red', name='photo.jpg'):
    """Bring a picture in the way the pages do, and return its library id."""
    resp = client.post('/api/editor/upload', files={'file': (name, image_bytes(width, height, color))})
    return resp.json()['filename'][:32]


def saved_result(client, name='photo.jpg'):
    """A processed result kept in the library without being sent."""
    source = original(client, 800, 600, name=name)
    body = {'filename': f'{source}.jpg', 'original_filename': name, 'crop': {'x': 0, 'y': 0, 'width': 800, 'height': 450}}
    data = client.post('/api/editor/process', json=body).json()
    return client.post(data['url'] + '/save').json()['id']


def picture(client, image_id):
    return client.get(f'/api/library/{image_id}').json()


class TestListing:
    def test_an_empty_library(self, client):
        assert client.get('/api/library').json() == {'items': [], 'count': 0, 'bytes': 0, 'on_tv': 0, 'ready': 0, 'blocks': 0}

    def test_pictures_are_listed_with_their_status_and_counts(self, client):
        block = original(client, 2400, 3600, 'green', 'portrait.jpg')
        result = saved_result(client)
        data = client.get('/api/library').json()
        statuses = {item['id']: item['status'] for item in data['items']}
        assert statuses[block] == 'block'
        assert statuses[result] == 'ready'
        assert (data['count'], data['ready'], data['blocks'], data['on_tv']) == (3, 1, 2, 0)

    def test_one_picture_comes_with_its_recipe(self, client):
        data = picture(client, saved_result(client, 'Falls.jpg'))
        assert data['name'] == 'Falls_4k.jpg'
        assert data['recipe'] == {'type': 'single', 'preset': '4k', 'letterbox': False}
        assert data['parents'][0]['name'] == 'Falls.jpg'

    @pytest.mark.parametrize('image_id', ['0' * 32, 'photo', '..%5Clibrary.db'])
    def test_a_picture_the_library_does_not_hold(self, client, image_id):
        for path in ('', '/thumb', '/file'):
            assert client.get(f'/api/library/{image_id}{path}').status_code == 404

    def test_preview_and_file_are_served(self, client):
        image_id = original(client, 4000, 3000)
        thumb = client.get(f'/api/library/{image_id}/thumb')
        assert thumb.headers['content-type'] == 'image/jpeg'
        with Image.open(io.BytesIO(thumb.content)) as img:
            assert img.size == (480, 360)
        assert client.get(f'/api/library/{image_id}/file').content == image_bytes(4000, 3000)


class TestSend:
    def test_a_saved_result_is_sent_unchanged(self, client, tv, library):
        image_id = saved_result(client)
        resp = client.post(f'/api/library/{image_id}/send', json={})
        assert resp.json() == {'id': 'MY_F0001', 'selected': False, 'shown': False}
        assert tv.uploaded == [library.path(f'{image_id}.jpg').read_bytes()]
        assert picture(client, image_id)['status'] == 'on-tv'

    def test_send_without_a_body(self, client, tv):
        assert client.post(f'/api/library/{saved_result(client)}/send').status_code == 200
        assert tv.selected == []

    def test_send_and_show(self, client, tv):
        image_id = saved_result(client)
        resp = client.post(f'/api/library/{image_id}/send', json={'show': True})
        assert resp.json() == {'id': 'MY_F0001', 'selected': True, 'shown': True}
        assert picture(client, image_id)['last_shown_at']

    def test_a_picture_already_on_the_tv_is_not_sent_twice(self, client, tv):
        image_id = saved_result(client)
        client.post(f'/api/library/{image_id}/send')
        resp = client.post(f'/api/library/{image_id}/send')
        assert resp.status_code == 409
        assert len(tv.uploaded) == 1

    def test_an_original_is_sent_fitted_to_the_screen(self, client, tv):
        image_id = original(client, 4000, 3000, name='Falls.jpg')
        assert client.post(f'/api/library/{image_id}/send').status_code == 200
        with Image.open(io.BytesIO(tv.uploaded[0])) as img:
            assert img.size == (3840, 2160)
        items = client.get('/api/library').json()['items']
        assert {item['name']: item['status'] for item in items} == {'Falls_fit.jpg': 'on-tv', 'Falls.jpg': 'block'}

    def test_send_when_the_tv_is_unavailable(self, client, tv):
        tv.unavailable = True
        image_id = saved_result(client)
        resp = client.post(f'/api/library/{image_id}/send')
        assert resp.status_code == 503
        assert picture(client, image_id)['status'] == 'ready'

    def test_send_unknown_picture(self, client, tv):
        assert client.post('/api/library/' + '0' * 32 + '/send').status_code == 404
        assert tv.uploaded == []


class TestOnTheTV:
    def on_tv(self, client):
        image_id = saved_result(client)
        client.post(f'/api/library/{image_id}/send')
        return image_id

    def test_display(self, client, tv):
        image_id = self.on_tv(client)
        resp = client.post(f'/api/library/{image_id}/display')
        assert resp.json() == {'id': 'MY_F0001', 'shown': True}
        assert tv.selected == ['MY_F0001']
        assert picture(client, image_id)['last_shown_at']

    def test_display_while_the_tv_is_in_use_selects_without_showing(self, client, tv):
        tv.art_mode = False
        image_id = self.on_tv(client)
        assert client.post(f'/api/library/{image_id}/display').json()['shown'] is False
        assert picture(client, image_id)['last_shown_at'] is None

    def test_display_needs_the_picture_on_the_tv(self, client, tv):
        assert client.post(f'/api/library/{saved_result(client)}/display').status_code == 409
        assert tv.selected == []

    def test_display_when_the_tv_is_unavailable(self, client, tv):
        image_id = self.on_tv(client)
        tv.unavailable = True
        assert client.post(f'/api/library/{image_id}/display').status_code == 503

    def test_remove_from_the_tv_keeps_the_picture(self, client, tv, library):
        image_id = self.on_tv(client)
        resp = client.post(f'/api/library/{image_id}/remove-from-tv')
        assert resp.json() == {'id': image_id, 'removed': ['MY_F0001']}
        assert tv.deleted == ['MY_F0001']
        assert picture(client, image_id)['status'] == 'ready'
        assert library.path(f'{image_id}.jpg').is_file()

    def test_a_removed_picture_can_be_sent_again(self, client, tv):
        image_id = self.on_tv(client)
        client.post(f'/api/library/{image_id}/remove-from-tv')
        assert client.post(f'/api/library/{image_id}/send').json()['id'] == 'MY_F0002'
        assert picture(client, image_id)['tv'] == ['MY_F0002']

    def test_a_delete_the_tv_does_not_confirm_leaves_the_copy(self, client, tv):
        image_id = self.on_tv(client)
        tv.confirms_delete = False
        assert client.post(f'/api/library/{image_id}/remove-from-tv').status_code == 502
        assert picture(client, image_id)['status'] == 'on-tv'

    def test_remove_when_the_tv_is_unavailable(self, client, tv):
        image_id = self.on_tv(client)
        tv.unavailable = True
        assert client.post(f'/api/library/{image_id}/remove-from-tv').status_code == 503
        assert picture(client, image_id)['status'] == 'on-tv'

    def test_a_picture_on_the_tv_cannot_be_deleted(self, client):
        image_id = self.on_tv(client)
        resp = client.delete(f'/api/library/{image_id}')
        assert resp.status_code == 409
        assert 'Remove it from the TV first' in resp.json()['detail']
        assert picture(client, image_id)['id'] == image_id


class TestTagsAndDelete:
    def test_the_tag_can_be_removed_and_put_back(self, client):
        image_id = original(client, 2400, 3600)
        assert client.delete(f'/api/library/{image_id}/tags/building-block').json() == {'id': image_id, 'building_block': False}
        assert picture(client, image_id)['status'] == 'ready'
        assert client.put(f'/api/library/{image_id}/tags/building-block').json() == {'id': image_id, 'building_block': True}
        assert picture(client, image_id)['status'] == 'block'

    def test_tagging_an_unknown_picture(self, client):
        assert client.put('/api/library/' + '0' * 32 + '/tags/building-block').status_code == 404

    def test_delete(self, client, library):
        image_id = original(client)
        assert client.delete(f'/api/library/{image_id}').json() == {'id': image_id, 'deleted': True}
        assert client.get(f'/api/library/{image_id}').status_code == 404
        assert list(library.files.iterdir()) == []

    def test_a_deleted_original_can_no_longer_be_edited(self, client):
        image_id = original(client)
        client.delete(f'/api/library/{image_id}')
        assert client.get(f'/api/editor/source/{image_id}.jpg').status_code == 404

    def test_delete_unknown_picture(self, client):
        assert client.delete('/api/library/' + '0' * 32).status_code == 404
