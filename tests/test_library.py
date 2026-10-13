import io

import pytest
from PIL import Image

from app.library import Library, StillOnTV, is_building_block, summary


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / 'library')


def jpeg(width, height, color='red'):
    buf = io.BytesIO()
    Image.new('RGB', (width, height), color=color).save(buf, format='JPEG')
    return buf.getvalue()


def original(library, width=3840, height=2160, color='red', name='photo.jpg'):
    data = jpeg(width, height, color)
    return library.add_original(data, name, 'jpg', Image.open(io.BytesIO(data)), 'upload')


def result(library, *parents, color='blue', **extra):
    crop = {'x': 0, 'y': 0, 'width': 1600, 'height': 900}
    return library.keep_result(
        jpeg(3840, 2160, color), 'photo_4k.jpg', {'type': 'single', 'preset': '4k', 'letterbox': False},
        [(parent['id'], crop) for parent in parents], **extra,
    )


class TestBuildingBlockRule:
    @pytest.mark.parametrize('width, height', [
        (3840, 2160),   # the screen itself
        (1920, 1080),   # just large enough
        (7680, 4320),
        (3840, 2300),   # a little tall: the fit cuts off 6%
        (4200, 2160),   # a little wide: the fit cuts off 9%
    ])
    def test_a_picture_that_suits_the_screen_is_not_one(self, width, height):
        assert is_building_block(width, height) is False

    @pytest.mark.parametrize('width, height', [
        (2400, 3600),   # portrait
        (3000, 3000),   # square
        (4000, 3000),   # 4:3, the fit cuts off 25%
        (6000, 4000),   # 3:2, the fit cuts off 16%
        (5000, 2000),   # panorama
        (1919, 1080),   # screen-shaped but stretched more than 2x
        (1280, 720),
    ])
    def test_a_picture_that_needs_work_is_one(self, width, height):
        assert is_building_block(width, height) is True


class TestOriginals:
    def test_an_original_is_kept_with_a_preview(self, library):
        image = original(library, 4000, 3000, name='Falls.jpg')
        assert (image['kind'], image['name'], image['width'], image['height']) == ('original', 'Falls.jpg', 4000, 3000)
        assert library.path(image['file']).read_bytes() == jpeg(4000, 3000)
        with Image.open(library.thumb_path(image['id'])) as thumb:
            assert thumb.size == (480, 360)

    def test_the_same_bytes_twice_are_one_entry(self, library):
        first = original(library, name='first.jpg')
        again = original(library, name='second.jpg')
        assert again['id'] == first['id']
        assert again['name'] == 'first.jpg'
        assert len(library.list()) == 1

    def test_a_picture_that_needs_work_is_tagged(self, library):
        assert original(library, 2400, 3600)['status'] == 'block'

    def test_a_picture_that_suits_the_screen_is_ready(self, library):
        image = original(library)
        assert (image['status'], image['building_block']) == ('ready', False)

    def test_a_removed_tag_stays_removed(self, library):
        image = original(library, 2400, 3600)
        library.untag(image['id'])
        assert original(library, 2400, 3600)['building_block'] is False
        assert Library(library.root).get(image['id'])['status'] == 'ready'

    def test_a_tag_can_be_added_by_hand(self, library):
        image = original(library)
        library.tag(image['id'])
        library.tag(image['id'])
        assert library.get(image['id'])['status'] == 'block'

    def test_newest_comes_first(self, library):
        names = ['a.jpg', 'b.jpg', 'c.jpg']
        for name, color in zip(names, ['red', 'green', 'blue']):
            original(library, color=color, name=name)
        assert [image['name'] for image in library.list()] == names[::-1]

    def test_the_library_is_still_there_after_a_restart(self, library):
        image = original(library, 2400, 3600)
        reopened = Library(library.root)
        assert reopened.get(image['id']) == image
        assert [kept['id'] for kept in reopened.list()] == [image['id']]


class TestResults:
    def test_a_result_remembers_what_it_was_made_from(self, library):
        left, right = original(library, 2400, 3600, 'red', 'left.jpg'), original(library, 2000, 3000, 'green', 'right.jpg')
        kept = result(library, left, right)
        assert kept['kind'] == 'result'
        assert kept['recipe'] == {'type': 'single', 'preset': '4k', 'letterbox': False}
        assert [(p['id'], p['name']) for p in kept['parents']] == [(left['id'], 'left.jpg'), (right['id'], 'right.jpg')]
        assert kept['parents'][0]['crop'] == {'x': 0, 'y': 0, 'width': 1600, 'height': 900}

    def test_a_result_is_never_tagged_automatically(self, library):
        small = library.keep_result(jpeg(800, 600), 'small_fit.jpg', {'type': 'auto', 'crop': False}, [])
        assert (small['status'], small['building_block']) == ('ready', False)

    def test_keeping_the_same_result_again_changes_nothing(self, library):
        parent = original(library)
        first = result(library, parent, image_id='a' * 32)
        assert result(library, parent, image_id='a' * 32)['id'] == first['id']
        assert result(library, parent)['id'] == first['id']
        assert len(library.list()) == 2

    def test_an_original_that_has_been_used_is_no_longer_waiting(self, library):
        parent = original(library)
        result(library, parent)
        used = library.get(parent['id'])
        assert (used['status'], used['used_in']) == ('original', 1)

    def test_a_result_whose_original_is_already_gone_is_still_kept(self, library):
        kept = library.keep_result(jpeg(3840, 2160), 'orphan_4k.jpg', None, [('f' * 32, None)])
        assert kept['parents'] == [{'id': None, 'file': None, 'name': None, 'crop': None}]


class TestTVCopies:
    def test_a_sent_picture_is_on_the_tv(self, library):
        kept = result(library)
        library.record_sent(kept['id'], 'MY_F0001')
        on_tv = library.get(kept['id'])
        assert (on_tv['status'], on_tv['tv']) == ('on-tv', ['MY_F0001'])

    def test_a_picture_removed_from_the_tv_is_ready_again(self, library):
        kept = result(library)
        library.record_sent(kept['id'], 'MY_F0001')
        library.close_copy('MY_F0001')
        assert library.get(kept['id'])['status'] == 'ready'

    def test_reconcile_closes_copies_the_tv_no_longer_has(self, library):
        stays, goes = result(library), result(library, color='green')
        library.record_sent(stays['id'], 'MY_F0001')
        library.record_sent(goes['id'], 'MY_F0002')

        kept = library.reconcile({'MY_F0001', 'MY_F0099'}, None, True)

        assert kept == {'MY_F0001': {'id': stays['id'], 'name': 'photo_4k.jpg'}}
        assert library.get(stays['id'])['status'] == 'on-tv'
        assert library.get(goes['id'])['status'] == 'ready'

    def test_reconcile_notes_what_is_on_display(self, library):
        kept = result(library)
        library.record_sent(kept['id'], 'MY_F0001')
        library.reconcile({'MY_F0001'}, 'MY_F0001', True)
        assert library.get(kept['id'])['last_shown_at']

    def test_reconcile_does_not_count_a_picture_as_shown_while_the_tv_is_in_use(self, library):
        kept = result(library)
        library.record_sent(kept['id'], 'MY_F0001')
        library.reconcile({'MY_F0001'}, 'MY_F0001', False)
        assert library.get(kept['id'])['last_shown_at'] is None

    def test_an_id_the_tv_hands_out_again_belongs_to_the_new_picture(self, library):
        old, new = result(library), result(library, color='green')
        library.record_sent(old['id'], 'MY_F0001')
        library.record_sent(new['id'], 'MY_F0001')
        assert library.get(old['id'])['tv'] == []
        assert library.get(new['id'])['tv'] == ['MY_F0001']

    def test_a_tagged_picture_on_the_tv_counts_as_both(self, library):
        kept = result(library)
        library.tag(kept['id'])
        library.record_sent(kept['id'], 'MY_F0001')
        counts = summary(library.list())
        assert (counts['on_tv'], counts['blocks'], counts['ready']) == (1, 1, 0)


class TestDelete:
    def test_delete_removes_the_files(self, library):
        image = original(library)
        library.delete(image['id'])
        assert library.get(image['id']) is None
        assert not library.path(image['file']).exists()
        assert not library.thumb_path(image['id']).exists()

    def test_a_picture_on_the_tv_cannot_be_deleted(self, library):
        kept = result(library)
        library.record_sent(kept['id'], 'MY_F0001')
        with pytest.raises(StillOnTV):
            library.delete(kept['id'])
        assert library.path(kept['file']).is_file()

    def test_deleting_an_original_keeps_what_was_made_from_it(self, library):
        parent = original(library)
        kept = result(library, parent)
        library.delete(parent['id'])
        left = library.get(kept['id'])
        assert left['parents'][0]['id'] is None
        assert left['parents'][0]['crop'] == {'x': 0, 'y': 0, 'width': 1600, 'height': 900}
        assert library.path(kept['file']).is_file()

    def test_a_deleted_picture_can_be_added_again(self, library):
        image = original(library)
        library.delete(image['id'])
        assert original(library)['id'] != image['id']


def test_summary_counts_each_filter_and_the_disk_space(library):
    block = original(library, 2400, 3600, 'green')
    ready = original(library)
    on_tv = result(library, ready)
    library.record_sent(on_tv['id'], 'MY_F0001')
    images = library.list()
    assert summary(images) == {
        'count': 3,
        'bytes': sum(library.path(image['file']).stat().st_size for image in (block, ready, on_tv)),
        'on_tv': 1,
        'ready': 0,
        'blocks': 1,
    }
