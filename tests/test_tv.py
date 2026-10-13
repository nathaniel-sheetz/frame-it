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

    television = Television()

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

        def get_artmode(self):
            return 'on'

        def select_image(self, content_id, show):
            television.calls.append(f'select {content_id}')

    monkeypatch.setattr(tv, 'SamsungTVWSConnection', Remote)
    monkeypatch.setattr(tv, 'SamsungTVArt', Art)
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
