from pathlib import Path

import pytest

from app import config


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A checkout of its own, so the real config.toml is never read."""
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    return tmp_path


def load(root, extra=''):
    (root / 'config.toml').write_text('[tv]\nhost = "192.0.2.1"\n' + extra)
    return config.load()


class TestLibraryPath:
    def test_it_is_a_folder_in_the_checkout_unless_set(self, root):
        assert load(root).library_path == root / 'data' / 'library'

    def test_a_relative_path_starts_at_the_checkout(self, root):
        assert load(root, '[library]\npath = "pictures/kept"\n').library_path == root / 'pictures' / 'kept'

    def test_an_absolute_path_is_used_as_it_is(self, root, tmp_path_factory):
        elsewhere = tmp_path_factory.mktemp('elsewhere')
        assert load(root, f'[library]\npath = "{elsewhere.as_posix()}"\n').library_path == elsewhere

    def test_a_path_can_start_at_the_home_folder(self, root):
        assert load(root, '[library]\npath = "~/frame-library"\n').library_path == Path.home() / 'frame-library'
