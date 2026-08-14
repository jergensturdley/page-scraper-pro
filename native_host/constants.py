"""Constants shared by the native helper."""

PROTOCOL_VERSION = 1
HELPER_VERSION = "1.0.0"
HOST_NAME = "com.pagescraperpro.ytdlp"
MAX_MESSAGE_BYTES = 1_048_576
MAX_URL_LENGTH = 8_192
REQUEST_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
ALLOWED_COMMANDS = frozenset({
    "health", "analyze", "choose_directory", "download", "cancel", "status"
})
ALLOWED_PRESETS = frozenset({"best", "1080p", "720p", "audio"})
