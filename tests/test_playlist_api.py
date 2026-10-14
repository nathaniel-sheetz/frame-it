import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import editor_routes, library_routes, playlist_routes
from app.library import Library
from app.player import Player
from app.playlists import Playlists

from .test_library_api import original
from .test_player import COLORS, Clock, FakeTV, begin, wait


@pytest.fixture
def tv():
    return FakeTV()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / 'library')


@pytest.fixture
def player(tv, library, clock):
    return Player(tv, library, Playlists(library), clock=clock)


@pytest.fixture
def client(tv, library, player, tmp_path):
    app = FastAPI()
    app.include_router(editor_routes.create_router(tv, library, tmp_path / 'processed'))
    app.include_router(library_routes.create_router(tv, library, player))
    app.include_router(playlist_routes.create_router(player, player.playlists, library))
    return TestClient(app)


def results(client, count):
    """Different pictures made for the TV and kept in the library, and their ids."""
    made = []
    for n, color in enumerate(COLORS[:count]):
        source = original(client, 800, 600, color, f'picture{n}.jpg')
        body = {'filename': f'{source}.jpg', 'original_filename': f'picture{n}.jpg',
                'crop': {'x': 0, 'y': 0, 'width': 800, 'height': 450}}
        data = client.post('/api/editor/process', json=body).json()
        made.append(client.post(data['url'] + '/save').json()['id'])
    return made


def playlist(client, pictures=(), **settings):
    """A new playlist holding these library pictures, and its id."""
    body = {'name': 'Evening', 'interval': 60, 'pictures': list(pictures), **settings}
    return client.post('/api/playlists', json=body).json()['playlist']['id']


def ids(client, playlist_id):
    return [p['id'] for p in client.get(f'/api/playlists/{playlist_id}').json()['pictures']]


class TestPlaylists:
    def test_no_playlists_yet(self, client):
        assert client.get('/api/playlists').json() == {'items': [], 'playing': None, 'ended': None}

    def test_a_playlist_is_made_with_its_pictures(self, client):
        held = results(client, 2)
        made = client.post('/api/playlists', json={'name': 'Evening', 'pictures': held}).json()
        assert (made['added'], made['refused']) == (2, [])
        assert made['playlist']['interval'] == 3600
        assert [p['id'] for p in made['playlist']['pictures']] == held
        assert [p['name'] for p in client.get('/api/playlists').json()['items']] == ['Evening']

    @pytest.mark.parametrize('body', [
        {'name': '   '}, {'name': 'x' * 101}, {'name': 'Evening', 'interval': 59},
        {'name': 'Evening', 'interval': 31 * 24 * 60 * 60}, {'name': 'Evening', 'interval': 90.5}])
    def test_a_playlist_that_cannot_be_made(self, client, body):
        assert client.post('/api/playlists', json=body).status_code in (400, 422)
        assert client.get('/api/playlists').json()['items'] == []

    def test_a_playlist_is_not_made_with_a_picture_the_library_does_not_hold(self, client):
        resp = client.post('/api/playlists', json={'name': 'Evening', 'pictures': ['0' * 32]})
        assert resp.status_code == 404
        assert client.get('/api/playlists').json()['items'] == []

    def test_one_that_is_not_there(self, client):
        assert client.get('/api/playlists/7').status_code == 404
        assert client.patch('/api/playlists/7', json={'name': 'Evening'}).status_code == 404
        assert client.delete('/api/playlists/7').status_code == 404
        assert client.post('/api/playlists/7/play').status_code == 404
        assert client.post('/api/playlists/7/pictures', json={'pictures': ['0' * 32]}).status_code == 404

    def test_settings_are_changed(self, client):
        playlist_id = playlist(client)
        changed = client.patch(f'/api/playlists/{playlist_id}', json={'name': 'Autumn', 'interval': 900, 'shuffle': True})
        assert changed.json() == {'id': playlist_id, 'name': 'Autumn', 'interval': 900, 'shuffle': True, 'pictures': []}

    def test_a_playlist_is_deleted_and_its_pictures_kept(self, client):
        held = results(client, 1)
        playlist_id = playlist(client, held)
        assert client.delete(f'/api/playlists/{playlist_id}').json() == {'id': playlist_id, 'deleted': True}
        assert client.get('/api/playlists').json()['items'] == []
        assert client.get(f'/api/library/{held[0]}').status_code == 200


class TestPictures:
    def test_pictures_are_added_at_the_end(self, client):
        first, second, third = results(client, 3)
        playlist_id = playlist(client, [first])
        added = client.post(f'/api/playlists/{playlist_id}/pictures', json={'pictures': [second, first, third]}).json()
        assert added['added'] == 2
        assert ids(client, playlist_id) == [first, second, third]

    def test_an_original_goes_in_as_its_fit_to_the_screen(self, client):
        source = original(client, 4000, 2300, name='Falls.jpg')
        playlist_id = playlist(client, [source])
        fitted = client.get(f'/api/playlists/{playlist_id}').json()['pictures']
        assert [p['name'] for p in fitted] == ['Falls_fit.jpg']
        kept = client.get(f'/api/library/{fitted[0]["id"]}').json()
        assert (kept['kind'], kept['width'], kept['height'], kept['status']) == ('result', 3840, 2160, 'ready')
        assert kept['parents'][0]['id'] == source
        # The same original a second time is the same fit, not another one.
        assert client.post(f'/api/playlists/{playlist_id}/pictures', json={'pictures': [source]}).json()['added'] == 0

    def test_a_building_block_is_left_out_and_named(self, client):
        block = original(client, 2400, 3600, 'green', 'portrait.jpg')
        ready = results(client, 1)
        playlist_id = playlist(client)
        added = client.post(f'/api/playlists/{playlist_id}/pictures', json={'pictures': [block] + ready}).json()
        assert (added['added'], added['refused']) == (1, ['portrait.jpg'])
        assert ids(client, playlist_id) == ready

    @pytest.mark.parametrize('pictures', [[], ['0' * 32], ['..%5Clibrary.db']])
    def test_pictures_that_cannot_be_added(self, client, pictures):
        playlist_id = playlist(client, results(client, 1))
        resp = client.post(f'/api/playlists/{playlist_id}/pictures', json={'pictures': pictures})
        assert resp.status_code in (404, 422)
        assert len(ids(client, playlist_id)) == 1

    def test_a_new_order(self, client):
        first, second, third = results(client, 3)
        playlist_id = playlist(client, [first, second, third])
        changed = client.patch(f'/api/playlists/{playlist_id}', json={'order': [third, first, second]})
        assert [p['id'] for p in changed.json()['pictures']] == [third, first, second]

    def test_an_order_made_before_the_playlist_changed_is_refused(self, client):
        first, second, third = results(client, 3)
        playlist_id = playlist(client, [first, second, third])
        resp = client.patch(f'/api/playlists/{playlist_id}', json={'order': [second, first]})
        assert resp.status_code == 400
        assert resp.json()['detail'] == 'The playlist has changed since that order was made.'
        assert ids(client, playlist_id) == [first, second, third]

    def test_a_picture_is_taken_out_and_stays_in_the_library(self, client):
        first, second = results(client, 2)
        playlist_id = playlist(client, [first, second])
        left = client.delete(f'/api/playlists/{playlist_id}/pictures/{first}').json()
        assert [p['id'] for p in left['pictures']] == [second]
        assert client.get(f'/api/library/{first}').status_code == 200


class TestPlaying:
    def test_a_playlist_starts_by_sending_its_pictures(self, client, tv, player):
        playlist_id = playlist(client, results(client, 2))
        started = client.post(f'/api/playlists/{playlist_id}/play').json()['playing']
        assert (started['state'], started['on_tv'], started['total']) == ('sending', 0, 2)
        assert (started['current'], started['next']) == (None, None)
        player.tick()
        assert client.get('/api/playing').json()['playing']['on_tv'] == 1

    def test_an_empty_playlist_is_not_started(self, client):
        resp = client.post(f'/api/playlists/{playlist(client)}/play')
        assert resp.status_code == 409
        assert client.get('/api/playing').json()['playing'] is None

    def test_what_is_playing(self, client, player, clock):
        first, second = results(client, 2)
        playlist_id = playlist(client, [first, second])
        begin(player, playlist_id)
        wait(player, clock, 15)
        assert client.get('/api/playing').json() == {
            'playing': {
                'playlist': {'id': playlist_id, 'name': 'Evening', 'interval': 60, 'shuffle': False},
                'state': 'playing', 'paused': False, 'on_tv': 2, 'total': 2,
                'current': {'id': first, 'name': 'picture0_4k.jpg'},
                'next': {'id': second, 'name': 'picture1_4k.jpg'},
                'remaining': 45, 'counting': True, 'problem': None,
            },
            'ended': None,
        }

    def test_next_back_pause_and_resume(self, client, player):
        first, second = results(client, 2)
        begin(player, playlist(client, [first, second]))
        assert client.post('/api/playing/next').json()['playing']['current']['id'] == second
        assert client.post('/api/playing/back').json()['playing']['current']['id'] == first
        assert client.post('/api/playing/pause').json()['playing']['paused'] is True
        assert client.post('/api/playing/resume').json()['playing']['paused'] is False

    def test_stopping(self, client, tv, player):
        begin(player, playlist(client, results(client, 2)))
        assert client.post('/api/playing/stop').json() == {'removed': 0, 'left': 0, 'playing': None, 'ended': None}
        assert len(tv.photos) == 2

    def test_stopping_and_taking_the_pictures_off_the_tv(self, client, tv, player):
        held = results(client, 2)
        begin(player, playlist(client, held))
        stopped = client.post('/api/playing/stop', json={'remove': True}).json()
        assert (stopped['removed'], stopped['left']) == (2, 0)
        assert tv.photos == []
        assert client.get(f'/api/library/{held[0]}').json()['status'] == 'ready'

    @pytest.mark.parametrize('action', ['next', 'back', 'pause', 'resume', 'stop'])
    def test_nothing_to_control_when_nothing_plays(self, client, action):
        resp = client.post(f'/api/playing/{action}')
        assert resp.status_code == 409
        assert resp.json()['detail'] == 'No playlist is playing.'

    def test_a_tv_out_of_reach_is_reported(self, client, tv, player):
        begin(player, playlist(client, results(client, 2)))
        tv.unavailable = True
        resp = client.post('/api/playing/next')
        assert resp.status_code == 503
        assert resp.json()['detail'] == 'Could not reach the TV.'

    def test_deleting_the_playlist_that_is_playing_stops_it(self, client, tv, player):
        playlist_id = playlist(client, results(client, 2))
        begin(player, playlist_id)
        client.delete(f'/api/playlists/{playlist_id}')
        assert client.get('/api/playing').json()['playing'] is None
        assert len(tv.photos) == 2

    def test_taking_out_the_picture_that_is_up_moves_on(self, client, player):
        first, second, third = results(client, 3)
        playlist_id = playlist(client, [first, second, third])
        begin(player, playlist_id)
        client.delete(f'/api/playlists/{playlist_id}/pictures/{first}')
        assert client.get('/api/playing').json()['playing']['current']['id'] == second

    def test_a_picture_added_while_playing_is_sent_at_the_next_look(self, client, tv, player):
        first, second = results(client, 2)
        playlist_id = playlist(client, [first])
        begin(player, playlist_id)
        client.post(f'/api/playlists/{playlist_id}/pictures', json={'pictures': [second]})
        player.tick()
        assert client.get(f'/api/library/{second}').json()['status'] == 'on-tv'


class TestDisplayedFromTheLibrary:
    def test_a_playlist_picture_is_carried_on_from(self, client, player):
        first, second, third = results(client, 3)
        begin(player, playlist(client, [first, second, third]))
        client.post(f'/api/library/{third}/display')
        playing = client.get('/api/playing').json()['playing']
        assert (playing['current']['id'], playing['next']['id']) == (third, first)

    def test_any_other_picture_stops_the_playlist(self, client, player):
        first, second, other = results(client, 3)
        begin(player, playlist(client, [first, second]))
        client.post(f'/api/library/{other}/send')
        client.post(f'/api/library/{other}/display')
        assert client.get('/api/playing').json() == {
            'playing': None, 'ended': 'Evening stopped because another picture was chosen.'}
