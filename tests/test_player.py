import random

import pytest

from app.library import Library
from app.player import PASS_OVER, RETRY, TICK, Player, Refused, neighbour, upcoming
from app.playlists import Playlists
from app.tv import TVUnavailable

from .test_playlists import picture


class FakeTV:
    def __init__(self):
        self.photos = []
        self.current = None
        self.art_mode = True
        self.unavailable = False
        self.confirms_upload = True
        self.uploads = 0
        self.deleted = []

    def _reach(self):
        if self.unavailable:
            raise TVUnavailable('Could not reach the TV.')

    def upload(self, jpeg):
        self._reach()
        if not self.confirms_upload:
            return None
        self.uploads += 1
        self.photos.append(f'MY_F{self.uploads:04d}')
        return self.photos[-1]

    def select(self, content_id):
        self._reach()
        self.current = content_id
        return self.art_mode

    def delete(self, content_id):
        self._reach()
        self.photos.remove(content_id)
        self.deleted.append(content_id)
        return True

    def now_showing(self):
        self._reach()
        return {'art_mode': self.art_mode, 'current': self.current}

    def photo_ids(self):
        self._reach()
        return set(self.photos)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


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
def playlists(library):
    return Playlists(library)


@pytest.fixture
def player(tv, library, playlists, clock):
    return Player(tv, library, playlists, clock=clock, rng=random.Random(4))


COLORS = ['red', 'green', 'blue', 'white', 'black', 'yellow']


@pytest.fixture
def evening(library, playlists):
    """A playlist of three pictures that changes every minute, and the pictures' ids."""
    playlist = playlists.create('Evening', 60)
    held = [picture(library, color) for color in COLORS[:3]]
    playlists.add(playlist['id'], held)
    return playlist['id'], held


def begin(player, playlist_id):
    """Start a playlist and let the clock turn until its pictures are sent and the first is up."""
    player.play(playlist_id)
    for _ in range(50):
        player.tick()
        if player.playlists.playing()['state'] == 'playing':
            return
    raise AssertionError('The playlist never started.')


def wait(player, clock, seconds):
    """Let time pass, with the clock looking at the TV as often as it does."""
    for _ in range(round(seconds / TICK)):
        clock.now += TICK
        player.tick()


def showing(tv, library):
    """The library id of the picture the TV has up."""
    return library.copy_of(tv.current)


class TestOrder:
    def test_in_order_the_next_picture_follows_and_the_last_wraps_round(self):
        assert neighbour(['a', 'b', 'c'], 'a', 1, None) == ('b', None)
        assert neighbour(['a', 'b', 'c'], 'c', 1, None) == ('a', None)
        assert neighbour(['a', 'b', 'c'], 'a', -1, None) == ('c', None)

    def test_a_playlist_starts_with_its_first_picture(self):
        assert neighbour(['a', 'b', 'c'], None, 1, None) == ('a', None)

    def test_a_shuffle_shows_every_picture_once_before_any_again(self):
        ids, rng = ['a', 'b', 'c', 'd', 'e'], random.Random(1)
        current, cycle, shown = None, [], []
        for _ in range(len(ids) * 6):
            current, cycle = neighbour(ids, current, 1, cycle, rng)
            shown.append(current)
        passes = [shown[i:i + len(ids)] for i in range(0, len(shown), len(ids))]
        assert all(sorted(one) == ids for one in passes)
        assert len({tuple(one) for one in passes}) > 1
        assert all(a != b for a, b in zip(shown, shown[1:]))

    def test_a_shuffle_goes_back_as_far_as_its_first_picture(self):
        cycle = ['c', 'a', 'b']
        assert neighbour(['a', 'b', 'c'], 'b', -1, cycle) == ('a', cycle)
        assert neighbour(['a', 'b', 'c'], 'c', -1, cycle) == (None, cycle)

    def test_a_picture_added_to_a_shuffle_comes_after_the_one_that_is_up(self):
        for seed in range(20):
            _, cycle = neighbour(['a', 'b', 'c', 'new'], 'a', 1, ['c', 'a', 'b'], random.Random(seed))
            assert sorted(cycle) == ['a', 'b', 'c', 'new']
            assert cycle.index('new') > cycle.index('a')

    def test_a_picture_taken_out_of_a_shuffle_is_passed_over(self):
        assert neighbour(['a', 'c'], 'a', 1, ['a', 'b', 'c']) == ('c', ['a', 'c'])

    def test_what_comes_next(self):
        assert upcoming(['a', 'b', 'c'], 'c', None) == 'a'
        assert upcoming(['a', 'b', 'c'], 'a', ['c', 'a', 'b']) == 'b'
        # After the last of a shuffle comes a new shuffle, which is not made yet.
        assert upcoming(['a', 'b', 'c'], 'b', ['c', 'a', 'b']) is None
        assert upcoming(['a'], 'a', None) is None


class TestStarting:
    def test_the_pictures_are_sent_and_the_first_put_up(self, tv, library, player, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        assert tv.uploads == 3
        assert showing(tv, library) == red
        assert all(p['on_tv'] for p in player.playlists.get(playlist_id)['pictures'])
        assert library.get(red)['last_shown_at']

    def test_nothing_is_put_up_until_every_picture_is_on_the_tv(self, tv, player, evening):
        player.play(evening[0])
        player.tick()
        player.tick()
        assert (tv.uploads, tv.current) == (2, None)
        assert player.status()['state'] == 'sending'
        assert (player.status()['on_tv'], player.status()['total']) == (2, 3)

    def test_a_picture_the_tv_already_has_is_not_sent_again(self, tv, library, player, evening):
        playlist_id, (red, green, blue) = evening
        library.record_sent(green, tv.upload(b'jpeg'))
        begin(player, playlist_id)
        assert tv.uploads == 3
        assert library.get(green)['tv'] == ['MY_F0001']

    def test_a_picture_removed_with_the_remote_is_sent_again(self, tv, library, player, evening):
        playlist_id, (red, green, blue) = evening
        library.record_sent(green, tv.upload(b'jpeg'))
        tv.photos.clear()
        begin(player, playlist_id)
        assert len(tv.photos) == 3
        assert library.get(green)['tv'] != ['MY_F0001']

    def test_it_waits_for_a_tv_that_cannot_be_reached(self, tv, library, player, evening):
        playlist_id, (red, green, blue) = evening
        player.play(playlist_id)
        player.tick()
        tv.unavailable = True
        assert player.tick() == RETRY
        assert player.status()['problem'] == 'Could not reach the TV.'
        tv.unavailable = False
        begin(player, playlist_id)
        assert tv.uploads == 3
        assert player.status()['problem'] is None

    def test_an_upload_the_tv_does_not_confirm_is_tried_again_later(self, tv, player, evening):
        tv.confirms_upload = False
        player.play(evening[0])
        # Each picture is tried once and passed over. With none to put up, the clock waits.
        assert [player.tick() for _ in range(4)][-1] == RETRY
        assert player.status()['problem'] == 'The TV did not confirm the upload.'
        assert player.status()['state'] == 'sending'

    def test_a_picture_the_tv_will_not_take_is_left_out_and_the_rest_play(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        library.record_sent(red, tv.upload(b'jpeg'))
        library.record_sent(blue, tv.upload(b'jpeg'))
        tv.confirms_upload = False
        begin(player, playlist_id)
        assert showing(tv, library) == red
        assert player.status()['problem'] == 'green.jpg is left out for now. The TV did not confirm the upload.'
        wait(player, clock, 60)
        assert showing(tv, library) == blue

    def test_starting_while_the_tv_is_in_use_selects_without_showing(self, tv, library, player, evening):
        playlist_id, (red, green, blue) = evening
        tv.art_mode = False
        begin(player, playlist_id)
        assert showing(tv, library) == red
        assert library.get(red)['last_shown_at'] is None
        assert player.status()['counting'] is False


class TestClock:
    def test_the_picture_changes_when_its_time_is_up(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        wait(player, clock, 45)
        assert showing(tv, library) == red
        assert player.status()['remaining'] == 15
        wait(player, clock, 15)
        assert showing(tv, library) == green
        assert player.status()['remaining'] == 60

    def test_after_the_last_picture_comes_the_first(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        seen = []
        for _ in range(4):
            wait(player, clock, 60)
            seen.append(showing(tv, library))
        assert seen == [green, blue, red, green]
        assert tv.uploads == 3

    def test_time_spent_watching_tv_does_not_count(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        wait(player, clock, 30)
        tv.art_mode = False
        wait(player, clock, 3600)
        assert showing(tv, library) == red
        assert player.status()['counting'] is False
        tv.art_mode = True
        wait(player, clock, 30)
        assert showing(tv, library) == red
        wait(player, clock, 15)
        assert showing(tv, library) == green

    def test_time_with_the_tv_out_of_reach_does_not_count(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        wait(player, clock, 30)
        tv.unavailable = True
        wait(player, clock, 3600)
        tv.unavailable = False
        wait(player, clock, 30)
        assert showing(tv, library) == red
        wait(player, clock, 15)
        assert showing(tv, library) == green

    def test_a_new_interval_applies_to_the_picture_that_is_up(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        wait(player, clock, 60 - TICK)
        player.playlists.edit(playlist_id, interval=120)
        wait(player, clock, 60)
        assert showing(tv, library) == red
        wait(player, clock, TICK)
        assert showing(tv, library) == green

    def test_a_playlist_of_one_picture_leaves_it_up(self, tv, library, playlists, player, clock):
        playlist = playlists.create('Alone', 60)
        playlists.add(playlist['id'], [picture(library, 'red')])
        begin(player, playlist['id'])
        # Putting it up again would restart its fade on the screen for nothing.
        tv.select = None
        wait(player, clock, 300)
        assert player.status()['state'] == 'playing'

    def test_where_it_had_got_to_survives_a_restart(self, tv, library, playlists, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        wait(player, clock, 60 + 30)
        restarted = Player(tv, library, playlists, clock=clock)
        assert restarted.status()['current']['id'] == green
        wait(restarted, clock, 30)
        assert showing(tv, library) == green
        wait(restarted, clock, 15)
        assert showing(tv, library) == blue


class TestControls:
    def test_next_and_back(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        wait(player, clock, 30)
        player.skip(1)
        assert showing(tv, library) == green
        # The new picture gets its whole time.
        assert player.status()['remaining'] == 60
        player.skip(-1)
        player.skip(-1)
        assert showing(tv, library) == blue

    def test_a_paused_playlist_holds_its_picture(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        wait(player, clock, 30)
        player.pause(True)
        wait(player, clock, 3600)
        assert showing(tv, library) == red
        assert (player.status()['paused'], player.status()['counting']) == (True, False)
        player.pause(False)
        wait(player, clock, 30)
        assert showing(tv, library) == red
        wait(player, clock, 15)
        assert showing(tv, library) == green

    def test_stopping_leaves_the_pictures_on_the_tv(self, tv, player, clock, evening):
        begin(player, evening[0])
        assert player.stop() == {'removed': 0, 'left': 0}
        assert player.status() is None
        wait(player, clock, 600)
        assert (len(tv.photos), tv.current) == (3, 'MY_F0001')

    def test_stopping_can_take_its_pictures_off_the_tv(self, tv, library, player, evening):
        playlist_id, (red, green, blue) = evening
        other = picture(library, 'white')
        library.record_sent(other, tv.upload(b'jpeg'))
        # One of its pictures was on the TV before the playlist was ever played.
        library.record_sent(green, tv.upload(b'jpeg'))
        begin(player, playlist_id)
        assert player.stop(remove=True) == {'removed': 3, 'left': 0}
        # A picture that is not in the playlist stays.
        assert tv.photos == ['MY_F0001']
        assert [library.get(image_id)['status'] for image_id in (red, green, blue, other)] == [
            'ready', 'ready', 'ready', 'on-tv']

    def test_what_could_not_be_taken_off_is_counted(self, tv, player, evening):
        begin(player, evening[0])
        tv.unavailable = True
        assert player.stop(remove=True) == {'removed': 0, 'left': 3}
        assert player.status() is None

    @pytest.mark.parametrize('action', [
        lambda player: player.skip(1), lambda player: player.pause(True), lambda player: player.stop()])
    def test_nothing_to_control_when_nothing_plays(self, player, action):
        with pytest.raises(Refused):
            action(player)

    def test_no_skipping_while_the_pictures_are_being_sent(self, player, evening):
        player.play(evening[0])
        player.tick()
        with pytest.raises(Refused):
            player.skip(1)

    def test_another_playlist_takes_over(self, tv, library, playlists, player, evening):
        begin(player, evening[0])
        autumn = playlists.create('Autumn', 60)
        white = picture(library, 'white')
        playlists.add(autumn['id'], [white])
        begin(player, autumn['id'])
        assert showing(tv, library) == white
        assert player.status()['playlist']['name'] == 'Autumn'


class TestChangesWhilePlaying:
    def test_a_new_order_applies_from_the_next_change(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        wait(player, clock, 30)
        player.playlists.edit(playlist_id, order=[blue, red, green])
        player.changed()
        wait(player, clock, 15)
        assert showing(tv, library) == red
        assert player.status()['next']['id'] == green
        wait(player, clock, 15)
        assert showing(tv, library) == green

    def test_an_added_picture_is_sent_straight_away_and_takes_its_turn(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        white = picture(library, 'white')
        player.playlists.add(playlist_id, [white])
        player.changed()
        player.tick()
        assert library.get(white)['status'] == 'on-tv'
        assert showing(tv, library) == red
        wait(player, clock, 180)
        assert showing(tv, library) == white

    def test_an_added_picture_the_tv_will_not_take_does_not_stop_the_clock(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        white = picture(library, 'white')
        player.playlists.add(playlist_id, [white])
        player.changed()
        tv.confirms_upload = False
        wait(player, clock, 30)
        assert player.status()['counting'] is True
        assert player.status()['remaining'] == 30
        assert player.status()['problem'] == 'white.jpg is left out for now. The TV did not confirm the upload.'
        wait(player, clock, 30)
        assert showing(tv, library) == green
        tv.confirms_upload = True
        wait(player, clock, PASS_OVER)
        assert library.get(white)['status'] == 'on-tv'
        assert player.status()['problem'] is None

    def test_taking_out_the_picture_that_is_up_moves_on(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        player.leaving(playlist_id, red)
        player.playlists.remove(playlist_id, red)
        assert showing(tv, library) == green
        wait(player, clock, 120)
        assert showing(tv, library) == green

    def test_taking_out_another_picture_leaves_the_one_that_is_up(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        player.leaving(playlist_id, green)
        player.playlists.remove(playlist_id, green)
        assert showing(tv, library) == red
        wait(player, clock, 60)
        assert showing(tv, library) == blue

    def test_a_playlist_with_no_pictures_left_stops(self, player, clock, evening):
        playlist_id, held = evening
        begin(player, playlist_id)
        for image_id in held:
            player.playlists.remove(playlist_id, image_id)
        player.tick()
        assert player.status() is None
        assert player.ended == 'Evening stopped because it has no pictures left.'


class TestAnotherPictureChosen:
    def test_one_of_the_playlists_own_is_carried_on_from(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        wait(player, clock, 45)
        chosen = library.get(blue)['tv'][0]
        tv.select(chosen)
        player.chosen(chosen, True)
        assert player.status()['current']['id'] == blue
        assert player.status()['remaining'] == 60
        wait(player, clock, 60)
        assert showing(tv, library) == red

    def test_one_from_outside_stops_the_playlist(self, tv, library, player, clock, evening):
        begin(player, evening[0])
        white = picture(library, 'white')
        outside = tv.upload(b'jpeg')
        library.record_sent(white, outside)
        tv.select(outside)
        player.chosen(outside, True)
        assert player.status() is None
        assert player.ended == 'Evening stopped because another picture was chosen.'
        wait(player, clock, 600)
        assert tv.current == outside

    def test_choosing_the_picture_that_is_already_up_changes_nothing(self, tv, player, clock, evening):
        begin(player, evening[0])
        wait(player, clock, 30)
        player.chosen(tv.current, True)
        assert player.status()['remaining'] == 30

    def test_a_choice_made_with_the_remote_is_noticed_at_the_second_look(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        tv.current = library.get(blue)['tv'][0]
        wait(player, clock, TICK)
        assert player.status()['current']['id'] == red
        wait(player, clock, TICK)
        assert player.status()['current']['id'] == blue
        wait(player, clock, 60)
        assert showing(tv, library) == red

    def test_art_from_outside_chosen_with_the_remote_stops_the_playlist(self, tv, player, clock, evening):
        begin(player, evening[0])
        tv.current = 'SAM-F0101'
        wait(player, clock, TICK)
        assert player.status() is not None
        wait(player, clock, TICK)
        assert player.status() is None
        assert player.ended == 'Evening stopped because another picture was chosen.'

    def test_a_tv_that_names_no_picture_is_acted_on_at_the_second_look(self, tv, player, clock, evening):
        begin(player, evening[0])
        tv.current = None
        wait(player, clock, TICK)
        assert player.status() is not None
        wait(player, clock, TICK)
        assert player.status() is None

    def test_a_glimpse_of_another_picture_is_not_acted_on(self, tv, player, clock, evening):
        begin(player, evening[0])
        ours, tv.current = tv.current, 'SAM-F0101'
        wait(player, clock, TICK)
        tv.current = ours
        wait(player, clock, TICK * 2)
        assert player.status()['current'] is not None

    def test_the_picture_that_is_up_being_removed_moves_on_and_it_is_sent_again(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        tv.photos.remove(tv.current)
        tv.current = 'SAM-F0101'
        wait(player, clock, TICK * 2)
        assert showing(tv, library) == green
        wait(player, clock, TICK)
        assert library.get(red)['tv'] == ['MY_F0004']
        assert player.status()['state'] == 'playing'

    def test_a_picture_whose_copy_is_gone_when_its_turn_comes_is_sent_again(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        tv.photos.remove(library.get(green)['tv'][0])
        wait(player, clock, 60)
        assert showing(tv, library) == green
        assert tv.current == 'MY_F0004'


class TestShuffle:
    def test_every_picture_gets_a_turn(self, tv, library, playlists, player, clock):
        playlist = playlists.create('Mixed', 60, shuffle=True)
        held = [picture(library, color) for color in COLORS]
        playlists.add(playlist['id'], held)
        begin(player, playlist['id'])
        seen = [showing(tv, library)]
        for _ in range(len(held) - 1):
            wait(player, clock, 60)
            seen.append(showing(tv, library))
        assert sorted(seen) == sorted(held)
        assert seen != held

    def test_what_comes_next_is_known_until_the_last_of_the_shuffle(self, tv, library, playlists, player, clock):
        playlist = playlists.create('Mixed', 60, shuffle=True)
        playlists.add(playlist['id'], [picture(library, color) for color in COLORS[:3]])
        begin(player, playlist['id'])
        following = player.status()['next']['id']
        wait(player, clock, 60)
        assert showing(tv, library) == following
        wait(player, clock, 60)
        assert player.status()['next'] is None

    def test_back_stops_at_the_first_of_the_shuffle(self, tv, library, playlists, player):
        playlist = playlists.create('Mixed', 60, shuffle=True)
        playlists.add(playlist['id'], [picture(library, color) for color in COLORS[:3]])
        begin(player, playlist['id'])
        first = showing(tv, library)
        with pytest.raises(Refused):
            player.skip(-1)
        player.skip(1)
        player.skip(-1)
        assert showing(tv, library) == first

    def test_turning_shuffle_on_keeps_the_picture_that_is_up(self, tv, library, player, clock, evening):
        playlist_id, (red, green, blue) = evening
        begin(player, playlist_id)
        player.playlists.edit(playlist_id, shuffle=True)
        player.changed()
        assert showing(tv, library) == red
        assert player.status()['next']['id'] in (green, blue)
        seen = []
        for _ in range(2):
            wait(player, clock, 60)
            seen.append(showing(tv, library))
        assert sorted(seen) == sorted([green, blue])

    def test_turning_shuffle_off_goes_back_to_the_order(self, tv, library, playlists, player, clock):
        playlist = playlists.create('Mixed', 60, shuffle=True)
        held = [picture(library, color) for color in COLORS[:3]]
        playlists.add(playlist['id'], held)
        begin(player, playlist['id'])
        playlists.edit(playlist['id'], shuffle=False)
        player.changed()
        up = held.index(showing(tv, library))
        wait(player, clock, 60)
        assert showing(tv, library) == held[(up + 1) % 3]

    def test_a_picture_chosen_by_hand_jumps_the_queue(self, tv, library, playlists, player, clock):
        playlist = playlists.create('Mixed', 60, shuffle=True)
        held = [picture(library, color) for color in COLORS]
        playlists.add(playlist['id'], held)
        begin(player, playlist['id'])
        first = showing(tv, library)
        last = player.playlists.playing()['cycle'][-1]
        chosen = library.get(last)['tv'][0]
        tv.select(chosen)
        player.chosen(chosen, True)
        seen = [first, last]
        for _ in range(len(held) - 2):
            wait(player, clock, 60)
            seen.append(showing(tv, library))
        assert sorted(seen) == sorted(held)

    def test_another_copy_of_the_picture_that_is_up_keeps_its_time(self, tv, library, playlists, player, clock):
        playlist = playlists.create('Mixed', 60, shuffle=True)
        playlists.add(playlist['id'], [picture(library, color) for color in COLORS[:3]])
        begin(player, playlist['id'])
        up, cycle = showing(tv, library), player.playlists.playing()['cycle']
        copy = tv.upload(b'jpeg')
        library.record_sent(up, copy)
        wait(player, clock, 30)
        tv.select(copy)
        player.chosen(copy, True)
        assert player.status()['current']['id'] == up
        assert player.status()['remaining'] == 30
        assert player.playlists.playing()['content_id'] == copy
        assert player.playlists.playing()['cycle'] == cycle
