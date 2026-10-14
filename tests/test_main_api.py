import importlib
import io
import sys
import threading

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config


class FakeTV:
    def __init__(self):
        self.photos = ['MY_F0001']
        self.current = 'MY_F0001'
        self.art_mode = True
        self.deleted = []

    def snapshot(self, cached_thumbs):
        return {
            'art_mode': self.art_mode,
            'current': self.current,
            'photos': [{'content_id': content_id} for content_id in self.photos],
            'thumbs': {content_id: b'jpeg' for content_id in self.photos if content_id not in cached_thumbs},
        }

    def select(self, content_id):
        return self.art_mode

    def delete(self, content_id):
        self.deleted.append(content_id)
        return True


@pytest.fixture
def main(tmp_path, monkeypatch):
    """The app as it is started, with its folders under tmp_path and a stand-in for the TV.

    The module reads its config and opens the library as it is imported, so
    everything it would touch is pointed elsewhere first.
    """
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    monkeypatch.setattr(config, 'THUMB_DIR', tmp_path / 'thumbs')
    monkeypatch.setattr(config, 'EDITOR_UPLOAD_DIR', tmp_path / 'uploads')
    monkeypatch.setattr(config, 'EDITOR_PROCESSED_DIR', tmp_path / 'processed')
    (tmp_path / 'config.toml').write_text('[tv]\nhost = "192.0.2.1"\n')
    sys.modules.pop('app.main', None)
    module = importlib.import_module('app.main')
    monkeypatch.setattr(module, 'tv', FakeTV())
    yield module
    sys.modules.pop('app.main', None)


@pytest.fixture
def client(main):
    return TestClient(main.app)


def sent(main, content_id='MY_F0001', color='red'):
    """A library picture with a copy on the TV, and its id."""
    buf = io.BytesIO()
    Image.new('RGB', (3840, 2160), color=color).save(buf, format='JPEG')
    image = main.library.keep_result(buf.getvalue(), 'Falls_4k.jpg', None, [])
    main.library.record_sent(image['id'], content_id)
    return image['id']


class TestGallery:
    def test_the_app_keeps_to_its_own_folders(self, main, tmp_path):
        assert main.library.root == tmp_path / 'data' / 'library'

    def test_each_photo_comes_with_the_library_picture_it_is_a_copy_of(self, main, client):
        image_id = sent(main)
        main.tv.photos = ['MY_F0001', 'MY_F0002']
        items = client.get('/api/art').json()['items']
        assert [item['id'] for item in items] == ['MY_F0001', 'MY_F0002']
        assert items[0]['library'] == {'id': image_id, 'name': 'Falls_4k.jpg'}
        assert items[1]['library'] is None

    def test_the_picture_on_display_is_marked_as_shown(self, main, client):
        image_id = sent(main)
        client.get('/api/art')
        assert main.library.get(image_id)['last_shown_at']

    def test_a_picture_is_not_marked_as_shown_while_the_tv_is_in_use(self, main, client):
        image_id = sent(main)
        main.tv.art_mode = False
        client.get('/api/art')
        assert main.library.get(image_id)['last_shown_at'] is None

    def test_a_copy_removed_some_other_way_is_noticed(self, main, client):
        image_id = sent(main)
        main.tv.photos = []
        assert client.get('/api/art').json()['items'] == []
        kept = main.library.get(image_id)
        assert (kept['tv'], kept['status']) == ([], 'ready')

    def test_thumbnails_are_cached_and_dropped_with_their_photo(self, main, client):
        client.get('/api/art')
        assert (config.THUMB_DIR / 'MY_F0001.jpg').read_bytes() == b'jpeg'
        main.tv.photos = []
        client.get('/api/art')
        assert list(config.THUMB_DIR.iterdir()) == []

    def test_select_marks_the_picture_as_shown(self, main, client):
        image_id = sent(main)
        assert client.post('/api/art/MY_F0001/select').json() == {'id': 'MY_F0001', 'shown': True}
        assert main.library.get(image_id)['last_shown_at']

    def test_select_while_the_tv_is_in_use_does_not(self, main, client):
        image_id = sent(main)
        main.tv.art_mode = False
        assert client.post('/api/art/MY_F0001/select').json()['shown'] is False
        assert main.library.get(image_id)['last_shown_at'] is None

    def test_delete_takes_the_copy_and_keeps_the_picture(self, main, client):
        image_id = sent(main)
        client.get('/api/art')
        assert client.delete('/api/art/MY_F0001').json() == {'id': 'MY_F0001', 'deleted': True}
        assert main.tv.deleted == ['MY_F0001']
        assert not (config.THUMB_DIR / 'MY_F0001.jpg').exists()
        kept = main.library.get(image_id)
        assert (kept['tv'], kept['status']) == ([], 'ready')
        assert main.library.path(kept['file']).is_file()

    def test_the_library_page_is_served(self, client):
        resp = client.get('/library')
        assert resp.status_code == 200
        assert resp.headers['content-type'].startswith('text/html')


class TestPlaylists:
    @pytest.fixture
    def playing(self, main):
        """A playlist of two pictures on the TV, with the first one up. Returns their ids."""
        held = [sent(main, 'MY_F0001'), sent(main, 'MY_F0002', 'blue')]
        playlist = main.playlists.create('Evening')
        main.playlists.add(playlist['id'], held)
        main.playlists.begin(playlist['id'])
        main.playlists.note(state='playing', image_id=held[0], content_id='MY_F0001')
        return held

    def test_the_playlists_page_is_served(self, client):
        resp = client.get('/playlists')
        assert resp.status_code == 200
        assert resp.headers['content-type'].startswith('text/html')

    def test_the_clock_runs_while_the_app_does(self, main):
        with TestClient(main.app):
            assert any(thread.name == 'playlist' for thread in threading.enumerate())

    def test_setting_one_of_its_pictures_as_art_carries_the_playlist_on_from_it(self, client, playing):
        client.post('/api/art/MY_F0002/select')
        assert client.get('/api/playing').json()['playing']['current']['id'] == playing[1]

    def test_setting_any_other_picture_as_art_stops_the_playlist(self, client, playing):
        client.post('/api/art/MY_F0003/select')
        assert client.get('/api/playing').json() == {
            'playing': None, 'ended': 'Evening stopped because another picture was chosen.'}
