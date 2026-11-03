import json
import ssl
from dataclasses import replace

import pytest
from samsungtvws import exceptions

from app import tv
from app.config import Config
from app.tv import FrameTV, TVUnavailable

CFG = Config(tv_host='192.0.2.1', tv_port=8002, tv_name='Test', tv_timeout=5, server_host='127.0.0.1', server_port=0)


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    path = tmp_path / 'tv-token.txt'
    monkeypatch.setattr(tv, 'TOKEN_FILE', path)
    return path


@pytest.fixture
def television(monkeypatch):
    """A stand-in for the TV's two channels that records what was opened, in order."""
    class Television:
        def __init__(self):
            self.calls = []
            self.pairing_error = None
            self.photos = ['MY_F0002', 'MY_F0001']
            # How many times the connection that carries thumbnails is dropped as it opens.
            self.dropped_transfers = 0
            # How many times it is closed without an error instead, before anything was sent.
            self.empty_replies = 0
            # How many times it is closed without an error part way through a picture.
            self.cut_transfers = 0
            # How many times the TV stops sending part way through and leaves it open.
            self.stalled_transfers = 0
            # How many times a thumbnail header names a length that is not a number.
            self.bad_lengths = 0
            self.secured = False
            self.port = '50000'
            self.close_error = None
            self.transfers = []

    television = Television()

    class Transfer:
        """The second connection the TV sends thumbnails over, a few bytes at a time."""
        def __init__(self, data, *, then=None):
            # What a read does once the data has run out: return nothing (closed), or raise.
            self.data, self.then = data, then
            self.timeout = None
            self.closed = self.tls = False
            self.reads_after_close = 0

        def recv(self, size):
            if not self.data:
                if self.then:
                    raise self.then
                # Reading a closed connection again and again is the hang this guards against.
                self.reads_after_close += 1
                assert self.reads_after_close < 100, 'kept reading a connection the TV closed'
                return b''
            packet, self.data = self.data[:min(size, 3)], self.data[min(size, 3):]
            return packet

        def close(self):
            self.closed = True

    def connect(address, timeout):
        if address[1] > 65535:
            raise OverflowError('port must be 0-65535')
        content_ids = television.requested
        bad_length = False
        if television.bad_lengths:
            television.bad_lengths -= 1
            bad_length = True
        stream = b''
        for num, content_id in enumerate(content_ids):
            header = json.dumps({'fileID': content_id, 'fileType': 'jpg',
                                 'fileLength': None if bad_length else 4,
                                 'num': num, 'total': len(content_ids)}).encode()
            stream += len(header).to_bytes(4, 'big') + header
            if not bad_length:
                stream += b'jpeg'
        if television.dropped_transfers:
            television.dropped_transfers -= 1
            transfer = Transfer(b'', then=ssl.SSLEOFError('EOF occurred in violation of protocol'))
        elif television.empty_replies:
            television.empty_replies -= 1
            transfer = Transfer(b'')
        elif television.cut_transfers:
            television.cut_transfers -= 1
            transfer = Transfer(stream[:-2])
        elif television.stalled_transfers:
            television.stalled_transfers -= 1
            transfer = Transfer(stream[:-2], then=TimeoutError('timed out'))
        else:
            transfer = Transfer(stream)
        transfer.timeout = timeout
        television.transfers.append(transfer)
        return transfer

    class TLS:
        def wrap_socket(self, sock):
            sock.tls = True
            return sock

    class Remote:
        def __init__(self, host, *, endpoint, token_file, **kwargs):
            self.endpoint, self.token_file = endpoint, token_file

        def open(self):
            television.calls.append(f'open {self.endpoint}')
            if television.pairing_error:
                raise television.pairing_error
            with open(self.token_file, 'w') as f:
                f.write('12345678')

        def close(self):
            television.calls.append(f'close {self.endpoint}')

    class Art:
        def __init__(self, host, **kwargs):
            pass

        def open(self):
            television.calls.append('open art')

        def close(self):
            television.calls.append('close art')
            if television.close_error:
                raise television.close_error

        def get_artmode(self):
            return 'on'

        def select_image(self, content_id, show):
            television.calls.append(f'select {content_id}')

        def available(self, category):
            return [{'content_id': content_id} for content_id in television.photos]

        def get_current(self):
            return {'content_id': television.photos[0]}

        def get_uuid(self):
            return 'request-id'

        def _send_art_request(self, request):
            assert request['request'] == 'get_thumbnail_list'
            television.requested = [item['content_id'] for item in request['content_id_list']]
            television.calls.append(f'thumbnails {" ".join(television.requested)}')
            return {'conn_info': json.dumps({'ip': '192.0.2.1', 'port': television.port, 'secured': television.secured})}

    monkeypatch.setattr(tv, 'SamsungTVWSConnection', Remote)
    monkeypatch.setattr(tv, 'SamsungTVArt', Art)
    monkeypatch.setattr(tv, 'create_connection', connect)
    monkeypatch.setattr(tv, 'get_ssl_context', TLS)
    return television


class TestPairing:
    def test_an_unpaired_app_gets_a_token_before_using_the_art_channel(self, television, token_file):
        assert FrameTV(CFG).select('MY_F0001') is True

        assert token_file.read_text() == '12345678'
        assert television.calls == [
            'open samsung.remote.control', 'close samsung.remote.control',
            'open art', 'select MY_F0001', 'close art',
        ]

    def test_a_paired_app_does_not_pair_again(self, television, token_file):
        token_file.write_text('12345678')

        FrameTV(CFG).select('MY_F0001')

        assert television.calls == ['open art', 'select MY_F0001', 'close art']

    def test_an_unanswered_prompt_asks_for_it_to_be_accepted(self, television, token_file):
        television.pairing_error = exceptions.ConnectionFailure({'event': 'ms.channel.timeOut'})

        with pytest.raises(TVUnavailable, match='Choose "Allow" on the TV'):
            FrameTV(CFG).select('MY_F0001')

        assert not token_file.exists()
        assert 'open art' not in television.calls
        assert 'close samsung.remote.control' in television.calls

    def test_a_refused_prompt_says_so(self, television, token_file):
        television.pairing_error = exceptions.UnauthorizedError({'event': 'ms.channel.unauthorized'})

        with pytest.raises(TVUnavailable, match='refused the connection'):
            FrameTV(CFG).select('MY_F0001')

        assert 'open art' not in television.calls

    def test_a_tv_that_is_off_is_reported_as_unreachable(self, television, token_file):
        television.pairing_error = OSError('No route to host')

        with pytest.raises(TVUnavailable, match='Could not reach the TV at 192.0.2.1'):
            FrameTV(CFG).select('MY_F0001')

        assert 'open art' not in television.calls


class TestThumbnails:
    @pytest.fixture(autouse=True)
    def paired(self, token_file):
        token_file.write_text('12345678')

    def test_a_dropped_transfer_is_asked_for_again(self, television):
        television.dropped_transfers = 2

        assert FrameTV(CFG).thumbnail('MY_F0001') == b'jpeg'
        assert television.calls.count('thumbnails MY_F0001') == 3

    def test_a_transfer_that_keeps_dropping_is_given_up_on(self, television):
        television.dropped_transfers = 99

        with pytest.raises(TVUnavailable, match='Could not reach the TV'):
            FrameTV(CFG).thumbnail('MY_F0001')

        assert television.calls.count('thumbnails MY_F0001') == tv.THUMBNAIL_TRIES
        assert television.calls[-1] == 'close art'

    def test_a_transfer_closed_without_an_error_is_asked_for_again(self, television):
        television.empty_replies = 2

        assert FrameTV(CFG).thumbnail('MY_F0001') == b'jpeg'
        assert television.calls.count('thumbnails MY_F0001') == 3

    def test_a_transfer_that_keeps_closing_is_given_up_on(self, television):
        television.empty_replies = 99

        with pytest.raises(TVUnavailable, match='Could not reach the TV'):
            FrameTV(CFG).thumbnail('MY_F0001')

        assert television.calls.count('thumbnails MY_F0001') == tv.THUMBNAIL_TRIES

    def test_a_transfer_closed_part_way_through_is_asked_for_again(self, television):
        television.cut_transfers = 1

        assert FrameTV(CFG).thumbnail('MY_F0001') == b'jpeg'
        assert television.calls.count('thumbnails MY_F0001') == 2

    def test_a_stalled_transfer_is_given_up_on_without_asking_again(self, television):
        television.stalled_transfers = 99

        with pytest.raises(TVUnavailable, match='Could not reach the TV'):
            FrameTV(CFG).thumbnail('MY_F0001')

        assert television.calls.count('thumbnails MY_F0001') == 1
        assert television.calls[-1] == 'close art'

    def test_a_transfer_waits_no_longer_than_the_tv_timeout_and_is_closed(self, television):
        television.stalled_transfers = 1

        FrameTV(CFG).snapshot(set())

        [transfer] = television.transfers
        assert transfer.timeout == CFG.tv_timeout
        assert transfer.closed

    def test_a_secured_transfer_is_read_over_tls(self, television):
        television.secured = True

        assert FrameTV(CFG).thumbnail('MY_F0001') == b'jpeg'
        assert television.transfers[0].tls

    def test_the_list_comes_back_when_the_transfer_stalls(self, television):
        television.stalled_transfers = 1

        snap = FrameTV(CFG).snapshot(set())

        assert [p['content_id'] for p in snap['photos']] == ['MY_F0002', 'MY_F0001']
        assert snap['thumbs'] == {}

    def test_the_list_comes_back_when_every_transfer_closes(self, television):
        television.empty_replies = 99

        snap = FrameTV(CFG).snapshot(set())

        assert [p['content_id'] for p in snap['photos']] == ['MY_F0002', 'MY_F0001']
        assert snap['thumbs'] == {}

    def test_the_list_only_asks_for_thumbnails_it_does_not_have(self, television):
        snap = FrameTV(CFG).snapshot({'MY_F0002'})

        assert snap['thumbs'] == {'MY_F0001': b'jpeg'}
        assert television.calls == ['open art', 'thumbnails MY_F0001', 'close art']

    def test_the_list_survives_a_dropped_transfer(self, television):
        television.dropped_transfers = 1

        snap = FrameTV(CFG).snapshot(set())

        assert snap['thumbs'] == {'MY_F0002': b'jpeg', 'MY_F0001': b'jpeg'}

    def test_the_list_comes_back_without_thumbnails_the_tv_will_not_send(self, television):
        television.dropped_transfers = 99

        snap = FrameTV(CFG).snapshot(set())

        assert [p['content_id'] for p in snap['photos']] == ['MY_F0002', 'MY_F0001']
        assert snap['thumbs'] == {}
        assert snap['art_mode'] is True
        assert snap['current'] == 'MY_F0002'

    def test_a_length_that_is_not_a_number_is_given_up_on_and_the_tv_is_free(self, television):
        television.bad_lengths = 99
        frame = FrameTV(CFG)

        with pytest.raises(TVUnavailable, match='Could not reach the TV'):
            frame.thumbnail('MY_F0001')

        assert television.calls.count('thumbnails MY_F0001') == tv.THUMBNAIL_TRIES
        assert frame.select('MY_F0001') is True

    def test_a_port_outside_the_tcp_range_is_given_up_on(self, television):
        television.port = '70000'
        frame = FrameTV(CFG)

        with pytest.raises(TVUnavailable, match='Could not reach the TV'):
            frame.thumbnail('MY_F0001')

        assert television.calls.count('thumbnails MY_F0001') == tv.THUMBNAIL_TRIES
        assert frame.select('MY_F0001') is True


class TestBusy:
    @pytest.fixture(autouse=True)
    def paired(self, token_file):
        token_file.write_text('12345678')

    def test_a_request_behind_a_stuck_one_is_told_the_tv_is_busy(self, television):
        frame = FrameTV(replace(CFG, tv_timeout=0.05))
        frame._lock.acquire()

        with pytest.raises(TVUnavailable, match='still busy'):
            frame.select('MY_F0001')

        assert television.calls == []

    def test_the_tv_is_free_again_after_a_failed_request(self, television):
        frame = FrameTV(replace(CFG, tv_timeout=0.05))
        television.dropped_transfers = 99
        with pytest.raises(TVUnavailable):
            frame.thumbnail('MY_F0001')

        assert frame.select('MY_F0001') is True

    def test_the_tv_is_free_again_when_closing_the_connection_fails(self, television):
        television.close_error = OSError('reset')
        frame = FrameTV(CFG)

        with pytest.raises(OSError, match='reset'):
            frame.select('MY_F0001')

        television.close_error = None
        assert frame.select('MY_F0001') is True


class TestWatching:
    @pytest.fixture(autouse=True)
    def paired(self, token_file):
        token_file.write_text('12345678')

    def test_what_is_showing_is_asked_without_the_photo_list(self, television):
        assert FrameTV(CFG).now_showing() == {'art_mode': True, 'current': 'MY_F0002'}
        assert television.calls == ['open art', 'close art']

    def test_the_ids_of_the_photos_on_the_tv(self, television):
        assert FrameTV(CFG).photo_ids() == {'MY_F0001', 'MY_F0002'}
