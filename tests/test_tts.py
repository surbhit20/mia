from unittest.mock import MagicMock, patch

from mia.tts import synthesize


@patch("mia.tts.ElevenLabs")
def test_synthesize_defaults_to_pcm_24000(mock_elevenlabs_class):
    mock_client = MagicMock()
    mock_elevenlabs_class.return_value = mock_client
    mock_client.text_to_speech.convert.return_value = [b"chunk1", b"chunk2"]

    result = synthesize(api_key="test-key", text="hello")

    assert result == b"chunk1chunk2"
    mock_client.text_to_speech.convert.assert_called_once_with(
        voice_id="21m00Tcm4TlvDq8ikWAM",
        text="hello",
        output_format="pcm_24000",
    )


@patch("mia.tts.ElevenLabs")
def test_synthesize_respects_output_format_override(mock_elevenlabs_class):
    mock_client = MagicMock()
    mock_elevenlabs_class.return_value = mock_client
    mock_client.text_to_speech.convert.return_value = [b"mp3-bytes"]

    result = synthesize(api_key="test-key", text="hello", output_format="mp3_44100_128")

    assert result == b"mp3-bytes"
    mock_client.text_to_speech.convert.assert_called_once_with(
        voice_id="21m00Tcm4TlvDq8ikWAM",
        text="hello",
        output_format="mp3_44100_128",
    )


@patch("mia.tts.ElevenLabs")
def test_synthesize_respects_voice_id_override(mock_elevenlabs_class):
    mock_client = MagicMock()
    mock_elevenlabs_class.return_value = mock_client
    mock_client.text_to_speech.convert.return_value = [b"chunk"]

    synthesize(api_key="test-key", text="hello", voice_id="custom-voice-id")

    mock_client.text_to_speech.convert.assert_called_once_with(
        voice_id="custom-voice-id",
        text="hello",
        output_format="pcm_24000",
    )
