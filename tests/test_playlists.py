import io

import pytest
from PIL import Image

from app.library import Library
from app.playlists import DEFAULT_INTERVAL, Playlists


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / 'library')


@pytest.fixture
def playlists(library):
    return Playlists(library)


def picture(library, color, name=None):
    """A result in the library, ready for the TV, and its id."""
    buf = io.BytesIO()
    Image.new('RGB', (64, 36), color=color).save(buf, format='JPEG')
    return library.keep_result(buf.getvalue(), name or f'{color}.jpg', None, [])['id']


def ids(playlist):
    return [p['id'] for p in playlist['pictures']]


class TestPlaylists:
    def test_a_new_playlist_is_empty_and_changes_every_hour(self, playlists):
        playlist = playlists.create('  Evening   light ')
        assert playlist == {'id': playlist['id'], 'name': 'Evening light', 'interval': DEFAULT_INTERVAL,
                            'shuffle': False, 'pictures': []}

    def test_a_playlist_needs_a_name(self, playlists):
        with pytest.raises(ValueError):
            playlists.create('   ')

    def test_playlists_are_listed_oldest_first(self, playlists):
        playlists.create('Evening')
        playlists.create('Autumn')
        assert [p['name'] for p in playlists.all()] == ['Evening', 'Autumn']

    def test_one_that_is_not_there(self, playlists):
        assert playlists.get(7) is None
        assert playlists.edit(7, name='Evening') is None

    def test_settings_left_out_stay_as_they_are(self, playlists):
        playlist = playlists.create('Evening', 300, True)
        changed = playlists.edit(playlist['id'], interval=600)
        assert (changed['name'], changed['interval'], changed['shuffle']) == ('Evening', 600, True)
        assert playlists.edit(playlist['id'], shuffle=False)['shuffle'] is False

    def test_a_deleted_playlist_does_not_hand_its_id_to_the_next(self, playlists):
        first = playlists.create('Evening')
        playlists.delete(first['id'])
        assert playlists.create('Autumn')['id'] != first['id']


class TestPictures:
    def test_pictures_play_in_the_order_they_were_added(self, library, playlists):
        playlist = playlists.create('Evening')
        red, green, blue = (picture(library, color) for color in ('red', 'green', 'blue'))
        assert playlists.add(playlist['id'], [red, green]) == 2
        assert playlists.add(playlist['id'], [blue]) == 1
        assert ids(playlists.get(playlist['id'])) == [red, green, blue]

    def test_a_picture_is_in_a_playlist_once(self, library, playlists):
        playlist = playlists.create('Evening')
        red, green = picture(library, 'red'), picture(library, 'green')
        playlists.add(playlist['id'], [red])
        assert playlists.add(playlist['id'], [red, green]) == 1
        assert ids(playlists.get(playlist['id'])) == [red, green]

    def test_each_picture_says_whether_the_tv_has_it(self, library, playlists):
        playlist = playlists.create('Evening')
        red, green = picture(library, 'red'), picture(library, 'green')
        playlists.add(playlist['id'], [red, green])
        library.record_sent(green, 'MY_F0001')
        assert playlists.get(playlist['id'])['pictures'] == [
            {'id': red, 'name': 'red.jpg', 'on_tv': False},
            {'id': green, 'name': 'green.jpg', 'on_tv': True},
        ]

    def test_a_new_order(self, library, playlists):
        playlist = playlists.create('Evening')
        red, green, blue = (picture(library, color) for color in ('red', 'green', 'blue'))
        playlists.add(playlist['id'], [red, green, blue])
        assert ids(playlists.edit(playlist['id'], order=[blue, red, green])) == [blue, red, green]
        # Whatever is added afterwards still goes at the end.
        white = picture(library, 'white')
        playlists.add(playlist['id'], [white])
        assert ids(playlists.get(playlist['id'])) == [blue, red, green, white]

    @pytest.mark.parametrize('order', [[0], [0, 1, 1], [0, 1, 2]])
    def test_an_order_that_is_not_the_playlists_pictures_changes_nothing(self, library, playlists, order):
        playlist = playlists.create('Evening')
        held = [picture(library, color) for color in ('red', 'green')]
        playlists.add(playlist['id'], held)
        known = held + [picture(library, 'blue')]
        with pytest.raises(ValueError):
            playlists.edit(playlist['id'], name='Renamed', order=[known[i] for i in order])
        unchanged = playlists.get(playlist['id'])
        assert (unchanged['name'], ids(unchanged)) == ('Evening', held)

    def test_a_picture_taken_out(self, library, playlists):
        playlist = playlists.create('Evening')
        red, green = picture(library, 'red'), picture(library, 'green')
        playlists.add(playlist['id'], [red, green])
        playlists.remove(playlist['id'], red)
        assert ids(playlists.get(playlist['id'])) == [green]
        assert library.get(red)

    def test_a_picture_deleted_from_the_library_leaves_its_playlists(self, library, playlists):
        playlist = playlists.create('Evening')
        red, green = picture(library, 'red'), picture(library, 'green')
        playlists.add(playlist['id'], [red, green])
        library.delete(red)
        assert ids(playlists.get(playlist['id'])) == [green]


class TestPlaying:
    def test_nothing_plays_until_a_playlist_is_begun(self, playlists):
        assert playlists.playing() is None
        assert playlists.stop() is None

    def test_a_playlist_begins_by_sending_its_pictures(self, playlists):
        playlist = playlists.create('Evening')
        playlists.begin(playlist['id'])
        assert playlists.playing() == {
            'playlist_id': playlist['id'], 'state': 'sending', 'paused': False, 'image_id': None,
            'content_id': None, 'shown_for': 0, 'cycle': None,
        }

    def test_where_it_has_got_to_is_kept(self, playlists):
        playlists.begin(playlists.create('Evening')['id'])
        playlists.note(state='playing', image_id='a', content_id='MY_F0001', shown_for=42.5,
                       cycle=['a', 'b'], paused=True)
        playing = playlists.playing()
        assert (playing['state'], playing['image_id'], playing['content_id']) == ('playing', 'a', 'MY_F0001')
        assert (playing['shown_for'], playing['cycle'], playing['paused']) == (42.5, ['a', 'b'], True)
        playlists.note(cycle=None)
        assert playlists.playing()['cycle'] is None

    def test_beginning_another_takes_over_from_the_start(self, playlists):
        first, second = playlists.create('Evening'), playlists.create('Autumn')
        playlists.begin(first['id'])
        playlists.note(state='playing', image_id='a', shown_for=30)
        playlists.begin(second['id'])
        playing = playlists.playing()
        assert (playing['playlist_id'], playing['state']) == (second['id'], 'sending')
        assert (playing['image_id'], playing['shown_for']) == (None, 0)

    def test_stopping_gives_back_where_it_had_got_to(self, playlists):
        playlist = playlists.create('Evening')
        playlists.begin(playlist['id'])
        assert playlists.stop()['playlist_id'] == playlist['id']
        assert playlists.playing() is None

    def test_deleting_the_playlist_that_is_running_stops_it(self, playlists):
        playlist = playlists.create('Evening')
        playlists.begin(playlist['id'])
        playlists.delete(playlist['id'])
        assert playlists.playing() is None
