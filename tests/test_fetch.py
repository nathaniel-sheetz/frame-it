import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import fetch
from app.fetch import FetchError

PICTURE = b'\xff\xd8 pretend this is a picture \xff\xd9'
LIMIT = 1000
# Every path the test site was asked for.
REQUESTS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, body, content_type='image/jpeg', length=True):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        if length:
            self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location):
        self.send_response(302)
        self.send_header('Location', location)
        self.end_headers()

    def do_GET(self):
        REQUESTS.append(self.path)
        try:
            self.route()
        except OSError:
            # The client hangs up early in the tests that refuse a download.
            pass

    def route(self):
        if self.path.startswith('/pictures/'):
            self.reply(PICTURE)
        elif self.path == '/':
            self.reply(PICTURE)
        elif self.path == '/redirect':
            self.redirect('/pictures/moved.jpg')
        elif self.path == '/redirect-with-body':
            self.send_response(302)
            self.send_header('Location', '/pictures/moved.jpg')
            self.end_headers()
            for _ in range(40):
                self.wfile.write(b'x' * LIMIT)
                time.sleep(0.05)
        elif self.path == '/slow-redirect':
            time.sleep(0.3)
            self.redirect('/pictures/late.jpg')
        elif self.path == '/short':
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', '100')
            self.end_headers()
            self.wfile.write(b'x' * 10)
        elif self.path == '/to-ftp':
            self.redirect('ftp://example.com/picture.jpg')
        elif self.path == '/page':
            self.reply(b'<html></html>', 'text/html; charset=utf-8')
        elif self.path == '/big':
            # Only the announcement: refusing this one cannot wait for the body.
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(LIMIT + 1))
            self.end_headers()
        elif self.path == '/big-unannounced':
            self.reply(b'x' * (LIMIT + 1), length=False)
        elif self.path == '/slow':
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.end_headers()
            for _ in range(40):
                self.wfile.write(b'x')
                time.sleep(0.05)
        else:
            self.send_error(404)


@pytest.fixture(scope='module')
def site():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()


@pytest.fixture
def open_guard(monkeypatch):
    """Lets the tests reach the local test site, which the real check refuses."""
    monkeypatch.setattr(fetch, '_is_public', lambda address: True)


class TestFetch:
    def test_returns_the_body_and_a_name_from_the_address(self, site, open_guard):
        data, name = fetch.fetch(f'{site}/pictures/Starry%20Night.jpg?width=1280', LIMIT)
        assert data == PICTURE
        assert name == 'Starry Night.jpg'

    def test_name_falls_back_when_the_address_has_none(self, site, open_guard):
        assert fetch.fetch(site, LIMIT)[1] == 'image.jpg'

    def test_follows_a_redirect_and_names_the_file_after_where_it_led(self, site, open_guard):
        data, name = fetch.fetch(f'{site}/redirect', LIMIT)
        assert data == PICTURE
        assert name == 'moved.jpg'

    def test_the_body_of_a_redirect_is_not_read(self, site, open_guard):
        # It is far over the limit and takes two seconds to arrive.
        started = time.monotonic()
        data, name = fetch.fetch(f'{site}/redirect-with-body', LIMIT)
        assert data == PICTURE
        assert name == 'moved.jpg'
        assert time.monotonic() - started < 1

    def test_a_redirect_that_arrives_too_late_is_not_followed(self, site, open_guard, monkeypatch):
        monkeypatch.setattr(fetch, 'TOTAL_TIMEOUT', 0.2)
        with pytest.raises(FetchError, match='too long') as e:
            fetch.fetch(f'{site}/slow-redirect', LIMIT)
        assert e.value.status == 504
        assert '/pictures/late.jpg' not in REQUESTS

    def test_a_download_cut_short_is_not_taken_for_the_whole_file(self, site, open_guard):
        with pytest.raises(FetchError, match='cut short') as e:
            fetch.fetch(f'{site}/short', LIMIT)
        assert e.value.status == 502

    def test_proxy_settings_are_ignored(self, site, open_guard, monkeypatch):
        for name in ('HTTP_PROXY', 'http_proxy'):
            monkeypatch.setenv(name, 'http://127.0.0.1:1')
        for name in ('NO_PROXY', 'no_proxy'):
            monkeypatch.delenv(name, raising=False)
        assert fetch.fetch(f'{site}/pictures/a.jpg', LIMIT)[0] == PICTURE

    def test_surrounding_spaces_are_ignored(self, site, open_guard):
        assert fetch.fetch(f'  {site}/pictures/a.jpg\n', LIMIT)[0] == PICTURE

    def test_a_web_page_is_refused_with_its_own_message(self, site, open_guard):
        with pytest.raises(FetchError, match='web page') as e:
            fetch.fetch(f'{site}/page', LIMIT)
        assert e.value.status == 400

    @pytest.mark.parametrize('path', ['/big', '/big-unannounced'])
    def test_too_large_is_refused_with_or_without_a_declared_length(self, site, open_guard, path):
        with pytest.raises(FetchError, match='too large') as e:
            fetch.fetch(site + path, LIMIT)
        assert e.value.status == 413

    def test_an_error_from_the_site_is_reported_with_its_code(self, site, open_guard):
        with pytest.raises(FetchError, match='404') as e:
            fetch.fetch(f'{site}/missing', LIMIT)
        assert e.value.status == 502

    def test_a_slow_trickle_is_cut_off(self, site, open_guard, monkeypatch):
        monkeypatch.setattr(fetch, 'TOTAL_TIMEOUT', 0.2)
        started = time.monotonic()
        with pytest.raises(FetchError, match='too long') as e:
            fetch.fetch(f'{site}/slow', LIMIT)
        assert e.value.status == 504
        assert time.monotonic() - started < 1.5

    def test_nothing_listening(self, open_guard):
        with pytest.raises(FetchError, match='Could not download') as e:
            fetch.fetch('http://127.0.0.1:1/picture.jpg', LIMIT)
        assert e.value.status == 502

    @pytest.mark.parametrize('url', [
        'ftp://example.com/picture.jpg',
        'file:///C:/Windows/win.ini',
        'data:image/png;base64,AAAA',
        'example.com/picture.jpg',
        'http:///picture.jpg',
        'http://[not-an-address/picture.jpg',
        '',
    ])
    def test_only_http_and_https_links(self, url):
        with pytest.raises(FetchError, match='Only http and https') as e:
            fetch.fetch(url, LIMIT)
        assert e.value.status == 400

    def test_a_redirect_cannot_leave_http(self, site, open_guard):
        with pytest.raises(FetchError, match='Could not download'):
            fetch.fetch(f'{site}/to-ftp', LIMIT)

    def test_nothing_but_http_and_https_can_be_opened(self):
        # The redirect above fails the same way whether or not ftp: is spoken,
        # so this is what would notice a handler for it being added.
        assert set(fetch._opener(time.monotonic()).handle_open) == {'http', 'https', 'unknown'}


class TestPublicOnly:
    def test_this_computer_is_refused(self, site):
        with pytest.raises(FetchError, match='public internet') as e:
            fetch.fetch(f'{site}/pictures/a.jpg', LIMIT)
        assert e.value.status == 400

    def test_this_computer_is_refused_before_anything_is_asked_of_it(self, site):
        asked = len(REQUESTS)
        with pytest.raises(FetchError, match='public internet'):
            fetch.fetch(f'{site}/pictures/a.jpg', LIMIT)
        assert len(REQUESTS) == asked

    def test_https_is_refused_before_the_handshake(self, site):
        # The test site does not speak TLS, so getting as far as the handshake
        # would be reported as a failed download instead.
        with pytest.raises(FetchError, match='public internet') as e:
            fetch.fetch(site.replace('http://', 'https://') + '/pictures/a.jpg', LIMIT)
        assert e.value.status == 400

    def test_a_neighbour_is_refused(self, site, open_guard, monkeypatch):
        monkeypatch.setattr(fetch, '_is_neighbour', lambda peer, own: True)
        with pytest.raises(FetchError, match='public internet') as e:
            fetch.fetch(f'{site}/pictures/a.jpg', LIMIT)
        assert e.value.status == 400

    def test_a_redirect_is_checked_too(self, site, monkeypatch):
        seen = []

        def first_only(address):
            seen.append(address)
            return len(seen) == 1

        monkeypatch.setattr(fetch, '_is_public', first_only)
        with pytest.raises(FetchError, match='public internet'):
            fetch.fetch(f'{site}/redirect', LIMIT)
        assert seen == ['127.0.0.1', '127.0.0.1']

    @pytest.mark.parametrize('address', [
        '127.0.0.1', '10.0.0.5', '172.16.0.1', '192.168.1.1', '169.254.169.254', '0.0.0.0',
        '100.64.0.1', '::1', 'fe80::1', 'fe80::1%eth0', 'fd00::1', '::ffff:127.0.0.1',
        '::ffff:192.168.1.1', '64:ff9b::7f00:1', '64:ff9b::c0a8:101', '::7f00:1', '::ffff:0:7f00:1',
        'fec0::1', 'not an address',
    ])
    def test_private_and_local_addresses_are_not_public(self, address):
        assert fetch._is_public(address) is False

    @pytest.mark.parametrize('address', [
        '93.184.216.34', '2606:4700:4700::1111', '::ffff:93.184.216.34', '64:ff9b::5db8:d822',
    ])
    def test_internet_addresses_are_public(self, address):
        assert fetch._is_public(address) is True

    @pytest.mark.parametrize('peer, own, expected', [
        ('2a02:1234:5678:9abc::10', '2a02:1234:5678:9abc:1:2:3:4', True),
        ('2a02:1234:5678:9abd::10', '2a02:1234:5678:9abc:1:2:3:4', False),
        ('93.184.216.34', '192.168.1.20', False),
        ('::ffff:93.184.216.34', '::ffff:192.168.1.20', False),
        ('2606:4700:4700::1111', 'not an address', True),
    ])
    def test_the_same_ipv6_network_is_a_neighbour(self, peer, own, expected):
        assert fetch._is_neighbour(peer, own) is expected
