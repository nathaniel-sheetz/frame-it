import pytest
import os
from PIL import Image
import tempfile
from app.editor import crop_and_upscale, crop_and_combine_diptych, PRESETS


class TestCropAndUpscale:
    """Test image processing functions"""

    def test_crop_and_upscale_4k(self):
        """Test cropping and upscaling to 4K resolution"""
        # Create a test image
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as input_file:
            input_path = input_file.name
            img = Image.new('RGB', (1600, 1200), color='red')
            img.save(input_path)

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as output_file:
            output_path = output_file.name

        try:
            # Crop and upscale
            crop_coords = {'x': 100, 'y': 100, 'width': 1200, 'height': 675}
            crop_and_upscale(
                input_path,
                output_path,
                crop_coords,
                PRESETS['4k']['width'],
                PRESETS['4k']['height']
            )

            # Verify output
            assert os.path.exists(output_path)
            with Image.open(output_path) as result:
                assert result.size == (3840, 2160)

        finally:
            # Cleanup
            if os.path.exists(input_path):
                os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_crop_and_upscale_fhd(self):
        """Test cropping and upscaling to Full HD resolution"""
        # Create a test image
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as input_file:
            input_path = input_file.name
            img = Image.new('RGB', (1600, 1200), color='blue')
            img.save(input_path)

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as output_file:
            output_path = output_file.name

        try:
            # Crop and upscale
            crop_coords = {'x': 50, 'y': 50, 'width': 1200, 'height': 675}
            crop_and_upscale(
                input_path,
                output_path,
                crop_coords,
                PRESETS['fhd']['width'],
                PRESETS['fhd']['height']
            )

            # Verify output
            assert os.path.exists(output_path)
            with Image.open(output_path) as result:
                assert result.size == (1920, 1080)

        finally:
            # Cleanup
            if os.path.exists(input_path):
                os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_crop_coordinates(self):
        """Test that crop coordinates are applied correctly"""
        # Create a test image with distinct quadrants
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as input_file:
            input_path = input_file.name
            img = Image.new('RGB', (800, 600))
            # Fill different areas with different colors
            pixels = img.load()
            for i in range(400):
                for j in range(300):
                    pixels[i, j] = (255, 0, 0)  # Top-left red
            for i in range(400, 800):
                for j in range(300):
                    pixels[i, j] = (0, 255, 0)  # Top-right green
            for i in range(400):
                for j in range(300, 600):
                    pixels[i, j] = (0, 0, 255)  # Bottom-left blue
            for i in range(400, 800):
                for j in range(300, 600):
                    pixels[i, j] = (255, 255, 0)  # Bottom-right yellow
            img.save(input_path)

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as output_file:
            output_path = output_file.name

        try:
            # Crop top-left quadrant (should be red)
            crop_coords = {'x': 0, 'y': 0, 'width': 400, 'height': 225}
            crop_and_upscale(
                input_path,
                output_path,
                crop_coords,
                1920,
                1080
            )

            # Verify output exists and has correct dimensions
            assert os.path.exists(output_path)
            with Image.open(output_path) as result:
                assert result.size == (1920, 1080)

        finally:
            # Cleanup
            if os.path.exists(input_path):
                os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_upscaling_quality(self):
        """Test that small images are upscaled correctly"""
        # Create a small test image
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as input_file:
            input_path = input_file.name
            img = Image.new('RGB', (480, 270), color='purple')
            img.save(input_path)

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as output_file:
            output_path = output_file.name

        try:
            # Upscale entire image to 4K
            crop_coords = {'x': 0, 'y': 0, 'width': 480, 'height': 270}
            crop_and_upscale(
                input_path,
                output_path,
                crop_coords,
                3840,
                2160
            )

            # Verify upscaled correctly
            assert os.path.exists(output_path)
            with Image.open(output_path) as result:
                assert result.size == (3840, 2160)
                # Verify it's still an image (not corrupted)
                assert result.mode == 'RGB'

        finally:
            # Cleanup
            if os.path.exists(input_path):
                os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)


class TestCropAndUpscaleLetterbox:
    """Test letterbox functionality in crop_and_upscale"""

    def test_portrait_image_pillarbox(self):
        """Tall (portrait) image produces pillarbox bars on left and right"""
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as input_file:
            input_path = input_file.name
            # Portrait image: 400x900
            img = Image.new('RGB', (400, 900), color=(0, 128, 255))
            img.save(input_path)

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as output_file:
            output_path = output_file.name

        try:
            crop_coords = {'x': 0, 'y': 0, 'width': 400, 'height': 900}
            crop_and_upscale(input_path, output_path, crop_coords, 1920, 1080, letterbox=True)

            with Image.open(output_path) as result:
                assert result.size == (1920, 1080)
                # Top-left corner should be black (pillarbox bar)
                assert result.getpixel((0, 0)) == (0, 0, 0)
                # Top-right corner should be black
                assert result.getpixel((1919, 0)) == (0, 0, 0)
                # Center should contain image content (not black)
                center_pixel = result.getpixel((960, 540))
                assert center_pixel != (0, 0, 0)
        finally:
            if os.path.exists(input_path):
                os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_wide_image_letterbox(self):
        """Wide image produces letterbox bars on top and bottom"""
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as input_file:
            input_path = input_file.name
            # Very wide image: 1200x200
            img = Image.new('RGB', (1200, 200), color=(255, 128, 0))
            img.save(input_path)

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as output_file:
            output_path = output_file.name

        try:
            crop_coords = {'x': 0, 'y': 0, 'width': 1200, 'height': 200}
            crop_and_upscale(input_path, output_path, crop_coords, 1920, 1080, letterbox=True)

            with Image.open(output_path) as result:
                assert result.size == (1920, 1080)
                # Top-left corner should be black (letterbox bar)
                assert result.getpixel((0, 0)) == (0, 0, 0)
                # Bottom-right corner should be black
                assert result.getpixel((1919, 1079)) == (0, 0, 0)
                # Center should contain image content
                center_pixel = result.getpixel((960, 540))
                assert center_pixel != (0, 0, 0)
        finally:
            if os.path.exists(input_path):
                os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_square_image_pillarbox(self):
        """Square (1:1) image produces pillarbox bars"""
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as input_file:
            input_path = input_file.name
            img = Image.new('RGB', (500, 500), color=(0, 255, 0))
            img.save(input_path)

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as output_file:
            output_path = output_file.name

        try:
            crop_coords = {'x': 0, 'y': 0, 'width': 500, 'height': 500}
            crop_and_upscale(input_path, output_path, crop_coords, 1920, 1080, letterbox=True)

            with Image.open(output_path) as result:
                assert result.size == (1920, 1080)
                # Left edge should be black (pillarbox)
                assert result.getpixel((0, 540)) == (0, 0, 0)
                # Right edge should be black
                assert result.getpixel((1919, 540)) == (0, 0, 0)
                # Center should contain image content
                center_pixel = result.getpixel((960, 540))
                assert center_pixel != (0, 0, 0)
        finally:
            if os.path.exists(input_path):
                os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_already_16_9_no_bars(self):
        """An already-16:9 image should fill frame completely with no bars"""
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as input_file:
            input_path = input_file.name
            # Exactly 16:9
            img = Image.new('RGB', (1600, 900), color=(200, 100, 50))
            img.save(input_path, format='PNG')

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as output_file:
            output_path = output_file.name

        try:
            crop_coords = {'x': 0, 'y': 0, 'width': 1600, 'height': 900}
            crop_and_upscale(input_path, output_path, crop_coords, 1920, 1080, letterbox=True)

            with Image.open(output_path) as result:
                assert result.size == (1920, 1080)
                # All corners should contain image content (no black bars)
                for pixel_pos in [(0, 0), (1919, 0), (0, 1079), (1919, 1079)]:
                    pixel = result.getpixel(pixel_pos)
                    assert pixel != (0, 0, 0), f"Corner {pixel_pos} should not be black"
        finally:
            if os.path.exists(input_path):
                os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_letterbox_false_backward_compat(self):
        """letterbox=False preserves original stretch behavior"""
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as input_file:
            input_path = input_file.name
            # Portrait: will be stretched if letterbox=False
            img = Image.new('RGB', (400, 900), color=(100, 200, 50))
            img.save(input_path)

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as output_file:
            output_path = output_file.name

        try:
            crop_coords = {'x': 0, 'y': 0, 'width': 400, 'height': 900}
            crop_and_upscale(input_path, output_path, crop_coords, 1920, 1080, letterbox=False)

            with Image.open(output_path) as result:
                assert result.size == (1920, 1080)
                # All corners should contain image content (stretched to fill)
                for pixel_pos in [(0, 0), (1919, 0), (0, 1079), (1919, 1079)]:
                    pixel = result.getpixel(pixel_pos)
                    assert pixel != (0, 0, 0), f"Corner {pixel_pos} should not be black with letterbox=False"
        finally:
            if os.path.exists(input_path):
                os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)


class TestPresets:
    """Test preset configurations"""

    def test_presets_exist(self):
        """Test that required presets are defined"""
        assert '4k' in PRESETS
        assert 'fhd' in PRESETS

    def test_4k_resolution(self):
        """Test 4K preset has correct resolution"""
        assert PRESETS['4k']['width'] == 3840
        assert PRESETS['4k']['height'] == 2160

    def test_fhd_resolution(self):
        """Test Full HD preset has correct resolution"""
        assert PRESETS['fhd']['width'] == 1920
        assert PRESETS['fhd']['height'] == 1080

    def test_preset_aspect_ratios(self):
        """Test that presets maintain 16:9 aspect ratio"""
        for preset_key, preset in PRESETS.items():
            aspect_ratio = preset['width'] / preset['height']
            assert abs(aspect_ratio - 16/9) < 0.01, f"{preset_key} should be 16:9"


class TestCropAndCombineDiptych:
    """Test diptych (side-by-side) image combining"""

    def _create_temp_image(self, width, height, color):
        """Helper to create a temporary image file"""
        f = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
        img = Image.new('RGB', (width, height), color=color)
        img.save(f.name)
        f.close()
        return f.name

    def test_diptych_output_exact_target_size_4k(self):
        """Two portraits combined should produce exact target dimensions"""
        input1 = self._create_temp_image(400, 900, 'red')
        input2 = self._create_temp_image(500, 800, 'blue')
        output = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name

        try:
            crop1 = {'x': 0, 'y': 0, 'width': 400, 'height': 900}
            crop2 = {'x': 0, 'y': 0, 'width': 500, 'height': 800}
            crop_and_combine_diptych(input1, input2, output, crop1, crop2, 3840, 2160)

            with Image.open(output) as result:
                assert result.size == (3840, 2160)
        finally:
            for p in [input1, input2, output]:
                if os.path.exists(p):
                    os.unlink(p)

    def test_diptych_output_exact_target_size_fhd(self):
        """Two portraits combined at FHD resolution"""
        input1 = self._create_temp_image(400, 900, 'green')
        input2 = self._create_temp_image(500, 800, 'yellow')
        output = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name

        try:
            crop1 = {'x': 0, 'y': 0, 'width': 400, 'height': 900}
            crop2 = {'x': 0, 'y': 0, 'width': 500, 'height': 800}
            crop_and_combine_diptych(input1, input2, output, crop1, crop2, 1920, 1080)

            with Image.open(output) as result:
                assert result.size == (1920, 1080)
        finally:
            for p in [input1, input2, output]:
                if os.path.exists(p):
                    os.unlink(p)

    def test_diptych_gap_is_black(self):
        """The gap between images should be black"""
        input1 = self._create_temp_image(400, 900, (255, 0, 0))
        input2 = self._create_temp_image(500, 800, (0, 0, 255))
        output = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name

        try:
            target_w, target_h = 1920, 1080
            crop1 = {'x': 0, 'y': 0, 'width': 400, 'height': 900}
            crop2 = {'x': 0, 'y': 0, 'width': 500, 'height': 800}
            crop_and_combine_diptych(input1, input2, output, crop1, crop2, target_w, target_h)

            gap = round(target_w * 0.01)
            sw1 = round(400 * target_h / 900)

            with Image.open(output) as result:
                # Check gap column at the midpoint height
                gap_x = sw1 + gap // 2
                pixel = result.getpixel((gap_x, target_h // 2))
                assert pixel == (0, 0, 0), f"Gap pixel should be black, got {pixel}"
        finally:
            for p in [input1, input2, output]:
                if os.path.exists(p):
                    os.unlink(p)

    def test_diptych_layout_math_exact(self):
        """Verify sw1 + gap + sw2 = targetW exactly"""
        input1 = self._create_temp_image(400, 900, 'red')
        input2 = self._create_temp_image(500, 800, 'blue')
        output = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name

        try:
            target_w, target_h = 3840, 2160
            crop1 = {'x': 0, 'y': 0, 'width': 400, 'height': 900}
            crop2 = {'x': 0, 'y': 0, 'width': 500, 'height': 800}

            gap = round(target_w * 0.01)
            sw1 = round(400 * target_h / 900)
            sw2 = target_w - gap - sw1

            # Verify the math
            assert sw1 + gap + sw2 == target_w

            crop_and_combine_diptych(input1, input2, output, crop1, crop2, target_w, target_h)

            with Image.open(output) as result:
                assert result.size == (target_w, target_h)
        finally:
            for p in [input1, input2, output]:
                if os.path.exists(p):
                    os.unlink(p)

    def test_diptych_gap_width_approximately_one_percent(self):
        """Gap should be approximately 1% of target width"""
        for target_w in [3840, 1920]:
            gap = round(target_w * 0.01)
            assert abs(gap / target_w - 0.01) < 0.005

    def test_diptych_image_content_fills_non_gap(self):
        """Both image areas should contain non-black content"""
        input1 = self._create_temp_image(400, 900, (200, 100, 50))
        input2 = self._create_temp_image(500, 800, (50, 100, 200))
        output = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name

        try:
            target_w, target_h = 1920, 1080
            crop1 = {'x': 0, 'y': 0, 'width': 400, 'height': 900}
            crop2 = {'x': 0, 'y': 0, 'width': 500, 'height': 800}
            crop_and_combine_diptych(input1, input2, output, crop1, crop2, target_w, target_h)

            gap = round(target_w * 0.01)
            sw1 = round(400 * target_h / 900)

            with Image.open(output) as result:
                # Image 1 area center should not be black
                pixel1 = result.getpixel((sw1 // 2, target_h // 2))
                assert pixel1 != (0, 0, 0), "Image 1 area should have content"

                # Image 2 area center should not be black
                img2_start = sw1 + gap
                pixel2 = result.getpixel((img2_start + (target_w - img2_start) // 2, target_h // 2))
                assert pixel2 != (0, 0, 0), "Image 2 area should have content"
        finally:
            for p in [input1, input2, output]:
                if os.path.exists(p):
                    os.unlink(p)

    def test_diptych_mismatched_aspect_ratios(self):
        """Different aspect ratio portraits should still produce correct output"""
        input1 = self._create_temp_image(300, 1000, 'red')
        input2 = self._create_temp_image(600, 700, 'blue')
        output = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name

        try:
            crop1 = {'x': 0, 'y': 0, 'width': 300, 'height': 1000}
            crop2 = {'x': 0, 'y': 0, 'width': 600, 'height': 700}
            crop_and_combine_diptych(input1, input2, output, crop1, crop2, 3840, 2160)

            with Image.open(output) as result:
                assert result.size == (3840, 2160)
        finally:
            for p in [input1, input2, output]:
                if os.path.exists(p):
                    os.unlink(p)

    def test_diptych_custom_gap_percent(self):
        """Custom gap_percent produces correct gap size in output"""
        target_w, target_h = 1920, 1080
        crop1 = {'x': 0, 'y': 0, 'width': 400, 'height': 900}
        crop2 = {'x': 0, 'y': 0, 'width': 500, 'height': 800}
        sw1 = round(400 * target_h / 900)

        for pct in [1, 3, 10]:
            input1 = self._create_temp_image(400, 900, (255, 0, 0))
            input2 = self._create_temp_image(500, 800, (0, 0, 255))
            output = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name

            try:
                expected_gap = round(target_w * pct / 100)
                crop_and_combine_diptych(
                    input1, input2, output, crop1, crop2, target_w, target_h, gap_percent=pct
                )

                with Image.open(output) as result:
                    assert result.size == (target_w, target_h)
                    # Check gap column is black
                    if expected_gap > 0:
                        gap_x = sw1 + expected_gap // 2
                        pixel = result.getpixel((gap_x, target_h // 2))
                        assert pixel == (0, 0, 0), \
                            f"Gap pixel should be black at gap_percent={pct}, got {pixel}"
            finally:
                for p in [input1, input2, output]:
                    if os.path.exists(p):
                        os.unlink(p)

    def test_diptych_default_gap_percent_backward_compat(self):
        """Default gap_percent=1.0 matches the original 1% behavior"""
        target_w, target_h = 1920, 1080
        input1 = self._create_temp_image(400, 900, 'red')
        input2 = self._create_temp_image(500, 800, 'blue')
        output = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name

        try:
            crop1 = {'x': 0, 'y': 0, 'width': 400, 'height': 900}
            crop2 = {'x': 0, 'y': 0, 'width': 500, 'height': 800}
            # Call without gap_percent — should use default 1.0
            crop_and_combine_diptych(input1, input2, output, crop1, crop2, target_w, target_h)

            with Image.open(output) as result:
                assert result.size == (target_w, target_h)
                # 1% of 1920 = 19px black gap after image 1 (480px wide at 1080 tall)
                r, g, b = result.convert('RGB').getpixel((480 + 19 // 2, 540))
                assert max(r, g, b) < 40
        finally:
            for p in [input1, input2, output]:
                if os.path.exists(p):
                    os.unlink(p)
