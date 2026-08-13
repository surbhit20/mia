from mia.command_buffer import CommandBuffer

def test_not_capturing_initially():
    buf = CommandBuffer()
    assert buf.is_capturing() is False

def test_start_then_append_then_silence_returns_command():
    buf = CommandBuffer()
    buf.start()
    assert buf.is_capturing() is True
    buf.append("block my")
    buf.append(" three pm slot")
    result = buf.on_silence()
    assert result == "block my three pm slot"
    assert buf.is_capturing() is False

def test_append_before_start_is_ignored():
    buf = CommandBuffer()
    buf.append("ignored text")
    assert buf.on_silence() is None

def test_on_silence_without_start_returns_none():
    buf = CommandBuffer()
    assert buf.on_silence() is None

def test_start_clears_previous_command():
    buf = CommandBuffer()
    buf.start()
    buf.append("first command")
    buf.on_silence()
    buf.start()
    result = buf.on_silence()
    assert result is None
