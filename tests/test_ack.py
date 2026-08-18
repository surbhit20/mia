from unittest.mock import patch

from mia.ack import ACK_PHRASE, acknowledgment_mp3


@patch("mia.ack.synthesize")
def test_generates_and_caches_on_first_call(mock_synthesize, tmp_path):
    mock_synthesize.return_value = b"fake-mp3"

    result = acknowledgment_mp3(api_key="k", cache_dir=tmp_path)

    assert result == b"fake-mp3"
    mock_synthesize.assert_called_once()
    # mp3, not PCM: this goes straight to Recall's output_audio endpoint.
    assert mock_synthesize.call_args.kwargs["output_format"] == "mp3_44100_128"
    assert mock_synthesize.call_args.kwargs["text"] == ACK_PHRASE
    assert list(tmp_path.glob("ack-*.mp3")), "clip was not written to the cache"


@patch("mia.ack.synthesize")
def test_second_call_reads_cache_without_calling_elevenlabs(mock_synthesize, tmp_path):
    # The whole point: generating at command time would cost ~1s, which is the
    # latency the acknowledgment exists to mask.
    mock_synthesize.return_value = b"fake-mp3"

    first = acknowledgment_mp3(api_key="k", cache_dir=tmp_path)
    mock_synthesize.reset_mock()
    second = acknowledgment_mp3(api_key="k", cache_dir=tmp_path)

    assert first == second == b"fake-mp3"
    mock_synthesize.assert_not_called()


@patch("mia.ack.synthesize")
def test_different_voice_does_not_reuse_another_voices_clip(mock_synthesize, tmp_path):
    mock_synthesize.side_effect = [b"voice-a-audio", b"voice-b-audio"]

    a = acknowledgment_mp3(api_key="k", voice_id="voice-a", cache_dir=tmp_path)
    b = acknowledgment_mp3(api_key="k", voice_id="voice-b", cache_dir=tmp_path)

    assert a == b"voice-a-audio"
    assert b == b"voice-b-audio"
    assert len(list(tmp_path.glob("ack-*.mp3"))) == 2


@patch("mia.ack.synthesize")
def test_corrupt_or_empty_cache_file_is_regenerated(mock_synthesize, tmp_path):
    # An empty file would otherwise be served as a zero-byte "clip" forever.
    mock_synthesize.return_value = b"fresh-audio"
    acknowledgment_mp3(api_key="k", cache_dir=tmp_path)
    cached = next(tmp_path.glob("ack-*.mp3"))
    cached.write_bytes(b"")
    mock_synthesize.reset_mock()

    result = acknowledgment_mp3(api_key="k", cache_dir=tmp_path)

    assert result == b"fresh-audio"
    mock_synthesize.assert_called_once()
