import os
import subprocess
import tempfile
import unittest

from utils import audio_enhancer


class TestAudioEnhancer(unittest.TestCase):
    def _make_audio(self, tmp_dir, name="input.mp3", content=b"audio"):
        path = os.path.join(tmp_dir, name)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def test_off_mode_returns_input_without_runner(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = self._make_audio(tmp_dir)
            calls = []

            result = audio_enhancer.enhance_audio(
                input_path,
                mode="off",
                runner=lambda *args, **kwargs: calls.append((args, kwargs)),
            )

        self.assertEqual(input_path, result)
        self.assertEqual([], calls)

    def test_missing_input_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            audio_enhancer.enhance_audio("/missing/audio.mp3", mode="mild")

    def test_invalid_mode_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = self._make_audio(tmp_dir)
            with self.assertRaises(ValueError):
                audio_enhancer.enhance_audio(input_path, mode="magic")

    def test_mild_builds_expected_ffmpeg_command(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = self._make_audio(tmp_dir)
            output_path = os.path.join(tmp_dir, "out.wav")
            captured = {}

            def fake_runner(cmd, **kwargs):
                captured["cmd"] = cmd
                captured["kwargs"] = kwargs
                with open(output_path, "wb") as f:
                    f.write(b"enhanced")

            result = audio_enhancer.enhance_audio(
                input_path,
                output_path=output_path,
                mode="mild",
                progress_callback=None,
                runner=fake_runner,
            )

        self.assertEqual(output_path, result)
        self.assertEqual("ffmpeg", captured["cmd"][0])
        self.assertIn("-ac", captured["cmd"])
        self.assertIn("1", captured["cmd"])
        self.assertIn("-ar", captured["cmd"])
        self.assertIn("16000", captured["cmd"])
        self.assertIn(audio_enhancer.FFMPEG_FILTERS[audio_enhancer.MODE_MILD], captured["cmd"])
        self.assertTrue(captured["kwargs"]["check"])

    def test_strong_alias_uses_strong_ffmpeg_filter(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = self._make_audio(tmp_dir)
            output_path = os.path.join(tmp_dir, "out.wav")
            captured = {}

            def fake_runner(cmd, **_kwargs):
                captured["cmd"] = cmd
                with open(output_path, "wb") as f:
                    f.write(b"enhanced")

            audio_enhancer.enhance_audio(
                input_path,
                output_path=output_path,
                mode="strong",
                progress_callback=None,
                runner=fake_runner,
            )

        self.assertIn(audio_enhancer.FFMPEG_FILTERS[audio_enhancer.MODE_STRONG_FFMPEG], captured["cmd"])

    def test_existing_output_is_reused_without_runner(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = self._make_audio(tmp_dir)
            output_path = os.path.join(tmp_dir, "cached.wav")
            self._make_audio(tmp_dir, "cached.wav", content=b"cached")
            calls = []

            result = audio_enhancer.enhance_audio(
                input_path,
                output_path=output_path,
                mode="mild",
                progress_callback=None,
                runner=lambda *args, **kwargs: calls.append((args, kwargs)),
            )

        self.assertEqual(output_path, result)
        self.assertEqual([], calls)

    def test_ffmpeg_failure_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = self._make_audio(tmp_dir)
            output_path = os.path.join(tmp_dir, "partial.wav")

            def failing_runner(_cmd, **_kwargs):
                with open(output_path, "wb") as f:
                    f.write(b"partial")
                raise subprocess.CalledProcessError(
                    returncode=1,
                    cmd="ffmpeg",
                    stderr="denoise failed",
                )

            with self.assertRaises(RuntimeError) as ctx:
                audio_enhancer.enhance_audio(
                    input_path,
                    output_path=output_path,
                    mode="mild",
                    progress_callback=None,
                    runner=failing_runner,
                )

        self.assertIn("denoise failed", str(ctx.exception))
        self.assertFalse(os.path.exists(output_path))


if __name__ == "__main__":
    unittest.main()
