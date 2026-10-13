import io

import pytest
from PIL import Image, ImageOps

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


class TestRename:
    def test_a_picture_can_be_renamed(self, library):
        image = original(library, name='IMG_4032.jpg')
        assert library.edit(image['id'], name='Niagara Falls')['name'] == 'Niagara Falls.jpg'
        assert library.list()[0]['name'] == 'Niagara Falls.jpg'

    @pytest.mark.parametrize('typed', ['Falls.jpg', 'Falls.JPG', '  Falls  ', 'Falls .jpg'])
    def test_the_extension_is_kept_once(self, library, typed):
        image = original(library, name='IMG_4032.jpg')
        assert library.edit(image['id'], name=typed)['name'] == 'Falls.jpg'

    def test_a_full_stop_in_the_name_is_not_an_extension(self, library):
        image = original(library, name='St. Jerome in his Study')
        assert library.edit(image['id'], name='St. Jerome')['name'] == 'St. Jerome'

    @pytest.mark.parametrize('typed', ['', '   ', '.jpg'])
    def test_a_picture_needs_a_name(self, library, typed):
        image = original(library, name='IMG_4032.jpg')
        with pytest.raises(ValueError):
            library.edit(image['id'], name=typed)
        assert library.get(image['id'])['name'] == 'IMG_4032.jpg'

    def test_a_refused_name_changes_nothing_else(self, library):
        image = original(library)
        with pytest.raises(ValueError):
            library.edit(image['id'], name='', creators=['Monet'])
        assert library.get(image['id'])['creators'] == []

    def test_renaming_a_picture_the_library_does_not_hold(self, library):
        assert library.edit('0' * 32, name='Falls') is None


class TestDetails:
    def test_a_new_picture_has_none(self, library):
        image = original(library)
        assert (image['creators'], image['subjects']) == ([], [])
        assert image['from_originals'] == {'creators': [], 'subjects': []}

    def test_details_are_kept_in_the_order_given(self, library):
        image = original(library)
        kept = library.edit(image['id'], creators=['Claude Monet'], subjects=['water lilies', 'pond', 'bridge'])
        assert kept['creators'] == ['Claude Monet']
        assert kept['subjects'] == ['water lilies', 'pond', 'bridge']
        assert Library(library.root).list()[0]['subjects'] == ['water lilies', 'pond', 'bridge']

    def test_details_are_tidied(self, library):
        image = original(library)
        kept = library.edit(image['id'], subjects=['  water   lilies ', '', 'Pond', 'pond', ' '])
        assert kept['subjects'] == ['water lilies', 'Pond']

    def test_what_is_left_out_stays_as_it_is(self, library):
        image = original(library, name='Falls.jpg')
        library.edit(image['id'], creators=['Frederic Church'], subjects=['waterfall'])
        kept = library.edit(image['id'], subjects=[])
        assert (kept['name'], kept['creators'], kept['subjects']) == ('Falls.jpg', ['Frederic Church'], [])

    def test_the_same_word_can_be_a_creator_and_a_subject(self, library):
        image = original(library)
        kept = library.edit(image['id'], creators=['Rembrandt'], subjects=['Rembrandt'])
        assert (kept['creators'], kept['subjects']) == (['Rembrandt'], ['Rembrandt'])

    def test_a_result_carries_the_details_of_its_original(self, library):
        parent = original(library)
        kept = result(library, parent)
        # Described after the result was made, and it still shows.
        library.edit(parent['id'], creators=['Claude Monet'], subjects=['pond'])
        for image in (library.get(kept['id']), library.list()[0]):
            assert (image['creators'], image['subjects']) == ([], [])
            assert image['from_originals'] == {'creators': ['Claude Monet'], 'subjects': ['pond']}

    def test_a_pair_carries_the_details_of_both_originals(self, library):
        left, right = original(library, color='red'), original(library, color='green')
        library.edit(left['id'], creators=['Monet'], subjects=['garden'])
        library.edit(right['id'], creators=['Renoir'], subjects=['Garden', 'boats'])
        pair = result(library, left, right)
        assert pair['from_originals'] == {'creators': ['Monet', 'Renoir'], 'subjects': ['garden', 'boats']}

    def test_details_come_through_a_result_that_was_edited_again(self, library):
        first = original(library)
        library.edit(first['id'], creators=['Hokusai'])
        second = result(library, first, color='blue')
        library.edit(second['id'], subjects=['wave'])
        third = result(library, second, color='green')
        assert third['from_originals'] == {'creators': ['Hokusai'], 'subjects': ['wave']}

    def test_a_detail_the_result_has_itself_is_not_repeated(self, library):
        parent = original(library)
        library.edit(parent['id'], subjects=['pond', 'bridge'])
        kept = library.edit(result(library, parent)['id'], subjects=['Bridge', 'evening'])
        assert kept['subjects'] == ['Bridge', 'evening']
        assert kept['from_originals']['subjects'] == ['pond']

    def test_an_original_does_not_take_details_from_what_was_made_from_it(self, library):
        parent = original(library)
        library.edit(result(library, parent)['id'], subjects=['detail'])
        assert library.get(parent['id'])['from_originals'] == {'creators': [], 'subjects': []}

    def test_a_deleted_original_takes_its_details_with_it(self, library):
        parent = original(library)
        library.edit(parent['id'], creators=['Monet'])
        kept = library.edit(result(library, parent)['id'], subjects=['pond'])
        library.delete(parent['id'])
        left = library.get(kept['id'])
        assert (left['subjects'], left['from_originals']['creators']) == (['pond'], [])

    def test_a_library_from_before_details_takes_them(self, library):
        image = original(library)
        with library._db() as db:
            db.execute('DROP TABLE details')
        reopened = Library(library.root)
        assert reopened.edit(image['id'], creators=['Monet'])['creators'] == ['Monet']


class TestView:
    def test_a_large_picture_is_shrunk_for_looking_at(self, library):
        image = original(library, 4000, 3000)
        with Image.open(library.view_path(image)) as view:
            assert (view.format, view.size) == ('JPEG', (1600, 1200))

    def test_a_small_picture_is_not_enlarged(self, library):
        image = original(library, 800, 600)
        with Image.open(library.view_path(image)) as view:
            assert view.size == (800, 600)

    def test_it_is_turned_the_way_the_picture_is_displayed(self, library):
        buf = io.BytesIO()
        exif = Image.Exif()
        exif[0x0112] = 6  # Orientation: shown rotated a quarter turn
        Image.new('RGB', (2000, 1000)).save(buf, format='JPEG', exif=exif)
        data = buf.getvalue()
        with Image.open(io.BytesIO(data)) as img:
            image = library.add_original(data, 'turned.jpg', 'jpg', ImageOps.exif_transpose(img), 'upload')
        with Image.open(library.view_path(image)) as view:
            assert view.size == (800, 1600)

    def test_it_is_made_once(self, library):
        image = original(library, 4000, 3000)
        path = library.view_path(image)
        path.write_bytes(b'kept')
        assert library.view_path(image).read_bytes() == b'kept'

    def test_it_goes_when_the_picture_is_deleted(self, library):
        image = original(library, 4000, 3000)
        path = library.view_path(image)
        library.delete(image['id'])
        assert not path.exists()

    def test_a_picture_whose_file_is_missing(self, library):
        image = original(library)
        library.path(image['file']).unlink()
        with pytest.raises(OSError):
            library.view_path(image)


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
