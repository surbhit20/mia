import struct
import CoreAudio
import objc
from Foundation import NSMutableData


def _default_input_device_id() -> int:
    address = CoreAudio.AudioObjectPropertyAddress(
        CoreAudio.kAudioHardwarePropertyDefaultInputDevice,
        CoreAudio.kAudioObjectPropertyScopeGlobal,
        CoreAudio.kAudioObjectPropertyElementMain,
    )
    output_data = NSMutableData.dataWithLength_(4)
    status, size, data = CoreAudio.AudioObjectGetPropertyData(
        CoreAudio.kAudioObjectSystemObject, address, 0, objc.NULL, 4, output_data
    )
    if status != 0:
        raise RuntimeError(f"AudioObjectGetPropertyData failed with status {status}")
    device_id = struct.unpack('<I', bytes(data))[0]
    return device_id


def is_mic_active() -> bool:
    device_id = _default_input_device_id()
    address = CoreAudio.AudioObjectPropertyAddress(
        CoreAudio.kAudioDevicePropertyDeviceIsRunningSomewhere,
        CoreAudio.kAudioObjectPropertyScopeGlobal,
        CoreAudio.kAudioObjectPropertyElementMain,
    )
    output_data = NSMutableData.dataWithLength_(4)
    status, size, data = CoreAudio.AudioObjectGetPropertyData(
        device_id, address, 0, objc.NULL, 4, output_data
    )
    if status != 0:
        raise RuntimeError(f"AudioObjectGetPropertyData failed with status {status}")
    is_running = struct.unpack('<I', bytes(data))[0]
    return bool(is_running)
