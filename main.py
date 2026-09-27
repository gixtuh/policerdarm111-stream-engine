# ai detected annihilate him
# licensed under mit i think
# gixtuh aka policerdarm111 1776-2036

# run pip install -r requirements.txt please i beg you

import time
import threading
import queue
import tkinter as tk
import requests
import scratchattach as scratch
from scratchattach.utils.encoder import Encoding
from PIL import Image, ImageGrab, ImageTk
import speech_recognition as sr
import pyaudio
import webrtcvad
import html


# *==============================================================*
# *CONFIG*
# *==============================================================*
USERNAME = "your username here"
PASSWORD = "your password here" # THIS IS FOR CLOUD VARIABLES TO WORK SO SCRATCH KNOWS THIS IS A REAL PERSON AND NOT A CLOUD ABUSER
PROJECT_ID = "1234567890" # change this to the id of your remixed project


# *==============================================================*
# *SCREEN STREAM*
# *==============================================================*
BASE_WIDTH = 56
BASE_HEIGHT = 43

# *Resolution level (1-7), stored as the 4th "info" digit and*
# *broadcast to Scratch so clients know the frame's dimensions.*
# *Each level scales BASE_WIDTH/BASE_HEIGHT. 7 = default, unscaled.*
RESOLUTION_SCALES = {
    "1": 1 / 3,
    "2": 1 / 2.5,
    "3": 1 / 2.1,
    "4": 1 / 2,
    "5": 1 / 1.5,
    "6": 1 / 1.1,
    "7": 1.0,
}

DEFAULT_RESOLUTION_DIGIT = "7"

# *WIDTH/HEIGHT are the ACTIVE capture resolution. They start at*
# *the default level and change whenever apply_resolution() runs.*
WIDTH = BASE_WIDTH
HEIGHT = BASE_HEIGHT

FPS = 15
FRAME_INTERVAL = 1 / FPS

VARIABLES = [
    "scr1",
    "scr2",
    "scr3",
    "scr4",
    "scr5",
    "scr6",
    "scr7",
]

CLOUD_CHUNK_SIZE = 256
CLOUD_CAPACITY = len(VARIABLES) * CLOUD_CHUNK_SIZE

TOTAL_PIXELS = WIDTH * HEIGHT

assert TOTAL_PIXELS == WIDTH*HEIGHT
assert CLOUD_CHUNK_SIZE <= 256
assert CLOUD_CAPACITY == 1792


# *==============================================================*
# *TKINTER GUI*
# *==============================================================*
# *The Tkinter event loop is explicitly serviced at 60 FPS.*
GUI_FPS = 60
GUI_INTERVAL = 1000 // GUI_FPS

SCALE = 16


# *==============================================================*
# *RLE CONFIG*
# *==============================================================*
MAX_RLE_RUN = 9


# *==============================================================*
# *TEXT VARIABLES*
# *==============================================================*
SPEECH_VARIABLE = "speech"
INFO_VARIABLE = "info"

TEXT_CLOUD_LIMIT = 256
SPEECH_SEPARATOR = "00"
COMMENT_CHECK_INTERVAL = 5

# *How many times to re-write the "speech" cloud variable per*
# *message, and the delay between each write. Repeating the*
# *write makes it far more likely every connected client*
# *actually receives the value.*
SPAM_COUNT = 10
SPAM_DELAY = 0.05


# *==============================================================*
# *MICROPHONE*
# *==============================================================*
MIC_RATE = 16000
MIC_CHANNELS = 1
FRAME_MS = 20
MIC_CHUNK = int(
    MIC_RATE * FRAME_MS / 1000
)

VAD_MODE = 3
SILENCE_DURATION = 0.8
MAX_PHRASE_SECONDS = 15
SPEECH_LANGUAGE = "en-US"


# *==============================================================*
# *GLOBAL STATE*
# *==============================================================*
running = True

frame_lock = threading.Lock()

latest_frame = None
latest_frame_dimensions = (WIDTH, HEIGHT)
frame_number = 0

latest_rle_length = 0
latest_rle_ratio = 0.0

# *Guards WIDTH/HEIGHT/TOTAL_PIXELS while apply_resolution() is*
# *changing the active capture resolution.*
resolution_lock = threading.Lock()

current_resolution_digit = DEFAULT_RESOLUTION_DIGIT
# Automatic resolution control.
AUTO_RESOLUTION = True

# How long to wait between attempts to increase resolution.
# The stream is only 2 FPS, so 5 seconds = roughly 10 frames.
RESOLUTION_RECOVERY_INTERVAL = 5.0

# Time of the last successful/attempted resolution recovery.
last_resolution_recovery = time.monotonic()

# *Set by apply_resolution() when the GUI thread needs to resize*
# *the canvas and force a fresh preview render.*
resolution_change_pending = threading.Event()

# *Commands:*
#
# *("speech_start",)*
# *("speech_result", text, username)*
# *("speech_failed",)*
# *("comment", text, username)*
# *("prompt_text", text, username)*
# *("set_afk", "0" or "1")*
# *("set_prompt_text", "0" or "1")*
# *("set_resolution", "1".."9")*
# *("end_stream",)*
message_queue = queue.Queue()

info_lock = threading.Lock()
screen_update_requested = threading.Event()
info_cloud_lock = threading.Lock()

afk_active = threading.Event()

# *This controls microphone pausing while the prompt is focused.*
prompt_focused = threading.Event()

# *Set while send_text_message() is spamming the "speech" variable.*
# *comment_loop() checks this and skips polling for new comments*
# *while it is set.*
comment_reading_paused = threading.Event()


# *==============================================================*
# *SCRATCH CONNECTIONS*
# *==============================================================*
print("Connecting to Scratch...")

session = scratch.login(USERNAME, PASSWORD)

screen_cloud = session.connect_cloud(
    PROJECT_ID
)

message_cloud = session.connect_cloud(
    PROJECT_ID
)

# *KEEP EXACTLY 4 DIGITS.*
#
# *Digit 0 = stream alive*
# *Digit 1 = AFK*
# *Digit 2 = speech/prompt focused*
# *Digit 3 = resolution level (1-9, see RESOLUTION_SCALES)*
#
# *1007 = live, not AFK, prompt unfocused, resolution level 7*
# *(default, unscaled BASE_WIDTH x BASE_HEIGHT)*
DEFAULT_INFO_VALUE = "1007"

known_info_value = DEFAULT_INFO_VALUE

print("Connected!")
print()
print("Project:", PROJECT_ID)
print(
    "Resolution:",
    f"{WIDTH}×{HEIGHT} (level {DEFAULT_RESOLUTION_DIGIT}, default)"
)
print(
    "Pixels:",
    TOTAL_PIXELS
)
print(
    "Screen FPS:",
    FPS
)
print(
    "GUI FPS:",
    GUI_FPS
)
print(
    "RLE capacity:",
    f"{CLOUD_CAPACITY} chars"
)
print(
    "Speech VAD:",
    f"{FRAME_MS} ms / {MIC_RATE} Hz"
)
print()


# *==============================================================*
# *TKINTER*
# *==============================================================*
root = tk.Tk()

root.title(
    f"Scratch Live Stream — {WIDTH}×{HEIGHT}"
)

root.resizable(
    True,
    True
)

canvas = tk.Canvas(
    root,
    width=WIDTH * SCALE,
    height=HEIGHT * SCALE,
    highlightthickness=0,
    background="black"
)

canvas.pack(
    fill="both",
    expand=True
)

status = tk.Label(
    root,
    text="Starting...",
    anchor="w"
)

status.pack(
    fill="x"
)


# *==============================================================*
# *PREVIEW*
# *==============================================================*
# *Use ONE Tkinter image instead of 2,408 rectangles.*
#
# *This is the important part for smooth window dragging.*
preview_image_item = canvas.create_image(
    0,
    0,
    anchor="nw"
)

preview_photo = None

last_rendered_frame_number = -1


# *==============================================================*
# *TEXT ENCODING*
# *==============================================================*
def encode_text_for_cloud(text, max_length=TEXT_CLOUD_LIMIT):
    """
    Encode text with scratchattach's Encoding.encode().
    If it is longer than max_length,
    shorten the original text until the encoded value fits.
    """
    text = str(text)

    try:
        encoded = Encoding.encode(
            text
        )

    except Exception as error:
        print(
            "Encoding error:",
            error
        )
        return None

    if len(encoded) <= max_length:
        return encoded

    accepted = ""

    for character in text:
        candidate = (
            accepted
            + character
        )

        try:
            candidate_encoded = Encoding.encode(
                candidate
            )

        except Exception:
            break

        if len(candidate_encoded) > max_length:
            break

        accepted = candidate

    if not accepted:
        return None

    return Encoding.encode(
        accepted
    )


# *==============================================================*
# *INFO VARIABLE*
# *==============================================================*
def set_info_digit(index, digit, immediate=False):
    """
    Change one digit of the local 4-digit info state.

    info is completely independent from scr1-scr7.
    If immediate=True, write info directly to Scratch.
    Otherwise, the change is only stored locally until something
    explicitly writes info.
    """
    global known_info_value

    digit = str(digit)

    if index == 3:
        valid_digit = digit in RESOLUTION_SCALES
    else:
        valid_digit = digit in ("0", "1")

    if not valid_digit:
        print(
            "Invalid info digit:",
            digit,
            "at index",
            index
        )
        return False

    with info_lock:
        current = (
            str(known_info_value)
            if known_info_value
            else DEFAULT_INFO_VALUE
        )

        current = current[:4].ljust(4, "0")

        chars = list(current)
        chars[index] = digit

        if running:
            chars[0] = "1"

        new_info_value = "".join(chars)
        known_info_value = new_info_value

    if immediate:
        try:
            message_cloud.set_var(
                INFO_VARIABLE,
                new_info_value
            )

            print(
                f"INFO IMMEDIATE: {new_info_value}"
            )

            return True

        except Exception as error:
            print(
                "Immediate info update error:",
                error
            )
            return False

    return True


def get_resolution_digit():
    """
    Read the resolution digit (index 3) currently baked into
    known_info_value, falling back to the default level if it's
    ever missing or invalid.
    """

    with info_lock:
        current = (
            str(known_info_value)
            if known_info_value
            else DEFAULT_INFO_VALUE
        )

    current = current[:4].ljust(
        4,
        "0"
    )

    digit = current[3]

    return (
        digit
        if digit in RESOLUTION_SCALES
        else DEFAULT_RESOLUTION_DIGIT
    )


def compose_info_value(alive, afk, focus):
    """
    Build a full 4-digit info value from the first three digits,
    preserving whatever resolution level is currently active.
    """

    return (
        f"{alive}{afk}{focus}"
        f"{get_resolution_digit()}"
    )


def set_info_speech_state(active):
    """
    Third digit of info.

    This is used by the actual microphone speech detector.
    """
    return set_info_digit(
        2,
        "1" if active else "0"
    )


def set_info_prompt_focus(active):
    """
    Third digit of info.

    The prompt uses the SAME digit as speech.

    1 = prompt focused
    0 = prompt unfocused
    """
    return set_info_digit(
        2,
        "1" if active else "0"
    )


# *==============================================================*
# *SEND TEXT TO SCRATCH*
# *==============================================================*
def send_text_message(
    text,
    speaker,
    source
):
    if not text:
        return

    encoded_speaker = encode_text_for_cloud(
        speaker
    )

    if encoded_speaker is None:
        print(
            f"[{source}] "
            "Could not encode username."
        )
        return

    remaining_budget = (
        TEXT_CLOUD_LIMIT
        - len(encoded_speaker)
        - len(SPEECH_SEPARATOR)
    )

    if remaining_budget <= 0:
        print(
            f"[{source}] "
            "No room left for speech after username."
        )
        return

    encoded_text = encode_text_for_cloud(
        text,
        max_length=remaining_budget
    )

    if encoded_text is None:
        print(
            f"[{source}] "
            "Could not encode text."
        )
        return

    combined_value = (
        encoded_speaker
        + SPEECH_SEPARATOR
        + encoded_text
    )

    # *Pause comment reading while we spam the speech variable so*
    # *a freshly-polled comment can't race the writes below.*
    comment_reading_paused.set()

    try:
        for attempt in range(SPAM_COUNT):
            try:
                message_cloud.set_var(
                    SPEECH_VARIABLE,
                    combined_value
                )

            except Exception as error:
                print(
                    f"[{source}] "
                    f"Cloud message error "
                    f"(attempt {attempt + 1}/{SPAM_COUNT}):",
                    error
                )

            if attempt < SPAM_COUNT - 1:
                time.sleep(
                    SPAM_DELAY
                )

        print(
            f"[{source}] "
            f"{speaker}: {text}"
        )

    finally:
        comment_reading_paused.clear()


# *==============================================================*
# *SCREEN RLE ENCODER*
# *==============================================================*
def rle_encode(frame):
    """
    Convert grayscale pixels into RLE.

    Example:

        00000000022227

    becomes:

        092721
    """

    if not frame:
        return ""

    encoded_parts = []

    length = len(frame)
    position = 0

    while position < length:
        shade = frame[position]

        run_length = 1

        while (
            position + run_length < length
            and frame[position + run_length] == shade
            and run_length < MAX_RLE_RUN
        ):
            run_length += 1

        encoded_parts.append(
            shade
            + str(run_length)
        )

        position += run_length

    return "".join(
        encoded_parts
    )


# *==============================================================*
# *SPLIT COMPRESSED FRAME*
# *==============================================================*
def split_rle_frame(encoded):
    if len(encoded) > CLOUD_CAPACITY:
        return None

    chunks = []

    for offset in range(
        0,
        len(encoded),
        CLOUD_CHUNK_SIZE
    ):
        chunks.append(
            encoded[
                offset:
                offset + CLOUD_CHUNK_SIZE
            ]
        )

    return chunks


# *==============================================================*
# *RESOLUTION CONTROL*
# *==============================================================*
def compute_resolution_dimensions(digit):
    scale = RESOLUTION_SCALES.get(
        digit
    )

    if scale is None:
        return None

    width = max(
        1,
        round(BASE_WIDTH * scale)
    )

    height = max(
        1,
        round(BASE_HEIGHT * scale)
    )

    return width, height


def apply_resolution(digit):
    """
    Change the ACTIVE capture/stream resolution to match the
    given resolution level (1-9). This is what actually resizes
    the screenshots — set_info_digit() only updates what gets
    broadcast to Scratch.
    """

    global WIDTH
    global HEIGHT
    global TOTAL_PIXELS
    global current_resolution_digit

    dimensions = compute_resolution_dimensions(
        digit
    )

    if dimensions is None:
        print(
            "Invalid resolution level:",
            digit
        )
        return False

    new_width, new_height = dimensions

    with resolution_lock:
        WIDTH = new_width
        HEIGHT = new_height
        TOTAL_PIXELS = WIDTH * HEIGHT
        current_resolution_digit = digit

    print(
        f"Resolution changed to level {digit}: "
        f"{new_width}x{new_height}"
    )

    # Ask the GUI thread to resize the canvas and force a fresh
    # preview render on the next frame it draws.
    resolution_change_pending.set()

    return True

def set_resolution_automatically(digit):
    global last_resolution_recovery

    if digit not in RESOLUTION_SCALES:
        return False

    if digit == get_resolution_digit():
        return False

    if not apply_resolution(digit):
        return False

    last_resolution_recovery = time.monotonic()
    return True

def handle_rle_size(compressed_length):
    """
    Automatically adjust resolution based on RLE size.

    If the current frame is too large, immediately decrease
    resolution by one level.

    If the current frame fits, periodically try increasing
    resolution by one level.

    Resolution 7 is the maximum and is never exceeded.
    """

    global last_resolution_recovery

    if not AUTO_RESOLUTION:
        return

    current_digit = get_resolution_digit()

    try:
        current_level = int(current_digit)
    except ValueError:
        current_level = 7

    # ----------------------------------------------------------
    # FRAME TOO LARGE
    # ----------------------------------------------------------

    if compressed_length > CLOUD_CAPACITY:

        # Immediately drop one level.
        if current_level > 1:
            new_level = str(
                current_level - 1
            )

            print(
                f"RLE too large "
                f"({compressed_length}/{CLOUD_CAPACITY}) "
                f"at resolution {current_level}; "
                f"dropping to {new_level}."
            )

            set_resolution_automatically(
                new_level
            )

        else:
            print(
                f"RLE still too large "
                f"({compressed_length}/{CLOUD_CAPACITY}) "
                f"even at minimum resolution 1."
            )

        return

    # ----------------------------------------------------------
    # FRAME FITS — TRY TO RECOVER UPWARD
    # ----------------------------------------------------------

    if current_level >= 7:
        return

    now = time.monotonic()

    if (
        now - last_resolution_recovery
        < RESOLUTION_RECOVERY_INTERVAL
    ):
        return

    new_level = str(
        current_level + 1
    )

    print(
        f"RLE fits "
        f"({compressed_length}/{CLOUD_CAPACITY}) "
        f"at resolution {current_level}; "
        f"trying higher resolution {new_level}."
    )

    set_resolution_automatically(
        new_level
    )

# *==============================================================*
# *SCREEN CAPTURE*
# *==============================================================*
def capture_frame():
    # Snapshot the active resolution once per capture so a
    # mid-loop resolution change can't tear a single frame.
    with resolution_lock:
        width = WIDTH
        height = HEIGHT

    screenshot = ImageGrab.grab()

    screenshot = screenshot.convert(
        "L"
    )

    screenshot = screenshot.resize(
        (width, height),
        Image.Resampling.BILINEAR
    )

    pixels = screenshot.load()

    encoded = []

    for y in range(height):
        for x in range(width):
            brightness = pixels[x, y]

            shade = round(
                brightness / 255 * 9
            )

            shade = max(
                0,
                min(9, shade)
            )

            encoded.append(
                str(shade)
            )

    frame = "".join(
        encoded
    )

    assert len(frame) == width * height

    return frame, width, height


# *==============================================================*
# *SCREEN CAPTURE THREAD*
# *==============================================================*
def capture_loop():
    global latest_frame
    global latest_frame_dimensions
    global frame_number

    next_frame = time.perf_counter()

    while running:
        try:
            frame, width, height = capture_frame()

            with frame_lock:
                latest_frame = frame
                latest_frame_dimensions = (width, height)
                frame_number += 1

            screen_update_requested.set()

        except Exception as error:
            print(
                "Capture error:",
                error
            )

        next_frame += FRAME_INTERVAL

        sleep_time = (
            next_frame
            - time.perf_counter()
        )

        if sleep_time > 0:
            time.sleep(
                sleep_time
            )
        else:
            next_frame = time.perf_counter()


# *==============================================================*
# *SCREEN CLOUD THREAD*
# *==============================================================*
def update_screen_variables(variables_to_send):
    """
    Upload ONLY scr1-scr7.

    info is intentionally NOT included here so the screen stream
    never waits for or gets blocked by an info update.
    """
    try:
        screen_cloud.set_vars(
            variables_to_send,
            intelligent_waits=False
        )
        return True

    except Exception as error:
        print(
            "Screen cloud update error:",
            error
        )
        return False

def update_info_resolution_digit(digit):
    """
    Change ONLY the 4th digit of info.

    This is called after every successful scr1-scr7 batch.
    """
    global known_info_value

    digit = str(digit)

    if digit not in RESOLUTION_SCALES:
        return False

    with info_lock:
        current = (
            str(known_info_value)
            if known_info_value
            else DEFAULT_INFO_VALUE
        )

        current = current[:4].ljust(4, "0")

        chars = list(current)
        chars[3] = digit

        if running:
            chars[0] = "1"

        known_info_value = "".join(chars)
        new_info_value = known_info_value

    try:
        message_cloud.set_var(
            INFO_VARIABLE,
            new_info_value
        )

        return True

    except Exception as error:
        print(
            "Info resolution update error:",
            error
        )
        return False

def screen_cloud_loop():
    global latest_rle_length
    global latest_rle_ratio

    last_uploaded_frame = None

    # Prevent the same captured frame from causing multiple
    # automatic resolution changes.
    last_rle_checked_frame_number = -1

    while running:
        # Wake when either a new frame or an info change happens.
        screen_update_requested.wait(
            timeout=0.01
        )
        screen_update_requested.clear()

        with frame_lock:
            frame = latest_frame
            current_frame_number = frame_number

        if frame is None:
            continue

        frame_changed = (
            frame != last_uploaded_frame
        )

        if not frame_changed:
            continue

        try:
            compressed = rle_encode(
                frame
            )

            compressed_length = len(
                compressed
            )

            raw_length = len(
                frame
            )

            ratio = (
                raw_length / compressed_length
                if compressed_length
                else 0
            )

            latest_rle_length = (
                compressed_length
            )

            latest_rle_ratio = (
                ratio
            )

            # ------------------------------------------------------
            # AUTOMATIC RESOLUTION CONTROL
            # ------------------------------------------------------
            #
            # Only evaluate automatic resolution ONCE for each
            # newly captured frame.
            #
            # This is important because an oversized frame can
            # remain in latest_frame while capture_loop() is still
            # producing the next frame.
            #
            if (
                current_frame_number
                != last_rle_checked_frame_number
            ):
                last_rle_checked_frame_number = (
                    current_frame_number
                )

                handle_rle_size(
                    compressed_length
                )

            # ------------------------------------------------------
            # FRAME TOO LARGE
            # ------------------------------------------------------
            #
            # The current frame was captured at the OLD resolution.
            # If automatic resolution just lowered the resolution,
            # this old frame is intentionally discarded.
            #
            if compressed_length > CLOUD_CAPACITY:
                print(
                    "RLE frame is too large:",
                    f"{compressed_length} chars",
                    "/",
                    f"{CLOUD_CAPACITY} available"
                )

                # Tell capture_loop() to produce a fresh frame
                # using the newly selected resolution.
                screen_update_requested.set()

                time.sleep(0.01)
                continue

            # ------------------------------------------------------
            # SPLIT FRAME
            # ------------------------------------------------------

            chunks = split_rle_frame(
                compressed
            )

            if chunks is None:
                print(
                    "Could not split RLE frame."
                )

                time.sleep(0.01)
                continue

            variables_to_send = {}

            for index, variable in enumerate(
                VARIABLES
            ):
                if index < len(chunks):
                    variables_to_send[
                        variable
                    ] = chunks[index]
                else:
                    variables_to_send[
                        variable
                    ] = "0"

            # ------------------------------------------------------
            # UPLOAD
            # ------------------------------------------------------

            if update_screen_variables(
                variables_to_send
            ):
                last_uploaded_frame = frame

                # ----------------------------------------------------------
                # INFO RESOLUTION DIGIT
                # ----------------------------------------------------------
                # Every successfully uploaded scr1-scr7 batch causes ONLY
                # info's 4th digit to be refreshed.
                #
                # This happens AFTER the screen batch, so info cannot delay
                # the actual frame upload.
                # ----------------------------------------------------------
                update_info_resolution_digit(
                    current_resolution_digit
                )

        except Exception as error:
            print(
                "Screen cloud error:",
                error
            )

            time.sleep(0.05)


# *==============================================================*
# *MESSAGE / INFO CLOUD THREAD*
# *==============================================================*
def message_cloud_loop():
    while running:
        try:
            command = message_queue.get(
                timeout=0.1
            )

        except queue.Empty:
            continue

        command_type = command[0]

        # *------------------------------------------------------*
        # *SPEECH START*
        # *------------------------------------------------------*
        if command_type == "speech_start":
            # *Third info digit = 1 while actually speaking.*
            set_info_speech_state(
                True
            )

        # *------------------------------------------------------*
        # *TRANSCRIPTION SUCCESS*
        # *------------------------------------------------------*
        elif command_type == "speech_result":
            _, text, speaker = command

            send_text_message(
                text,
                speaker,
                "MIC"
            )

            set_info_speech_state(
                False
            )

        # *------------------------------------------------------*
        # *TRANSCRIPTION FAILURE*
        # *------------------------------------------------------*
        elif command_type == "speech_failed":
            set_info_speech_state(
                False
            )

        # *------------------------------------------------------*
        # *SCRATCH COMMENT*
        # *------------------------------------------------------*
        elif command_type == "comment":
            _, text, speaker = command

            send_text_message(
                html.unescape(text),
                speaker,
                "COMMENT"
            )

        # *------------------------------------------------------*
        # *PROMPT TEXT*
        # *------------------------------------------------------*
        elif command_type == "prompt_text":
            _, text, speaker = command

            send_text_message(
                text,
                speaker,
                "PROMPT"
            )

        # *------------------------------------------------------*
        # *AFK TOGGLE*
        # *------------------------------------------------------*
        elif command_type == "set_afk":
            _, digit = command

            set_info_digit(
                1,
                digit
            )

        # *------------------------------------------------------*
        # *PROMPT TEXT*
        # *------------------------------------------------------*
        elif command_type == "set_prompt_text":
            _, digit = command

            set_info_prompt_focus(
                digit == "1"
            )

        # *------------------------------------------------------*
        # *RESOLUTION CHANGE*
        # *------------------------------------------------------*
        elif command_type == "set_resolution":
            _, digit = command

            if apply_resolution(digit):
                set_info_digit(
                    3,
                    digit
                )

        # *------------------------------------------------------*
        # *END STREAM*
        # *------------------------------------------------------*
        elif command_type == "end_stream":
            set_info_digit(
                0,
                "0"
            )


# *==============================================================*
# *INFO VARIABLE FORCE-WRITE ("SPAM") LOOP*
# *==============================================================*
# *This used to be a passive poll loop that READ "info" off the*
# *server and adopted whatever it found. That meant any outside*
# *write (another client, manual tampering, a race with one of*
# *the writers above) would get pulled back in as truth.*
#
# *This version does the opposite: it never reads the server. It*
# *just continuously re-asserts whatever known_info_value already*
# *is, on a fixed interval, no matter what is currently sitting*
# *on the cloud variable. Local state is authoritative; the*
# *server is treated as write-only.*


# *==============================================================*
# *SPEECH RECOGNITION*
# *==============================================================*
recognizer = sr.Recognizer()


def transcribe_audio(audio_data):
    try:
        print(
            "Transcribing speech..."
        )

        text = recognizer.recognize_google(
            audio_data,
            language=SPEECH_LANGUAGE
        )

        print(
            f"Recognized: {text}"
        )

        message_queue.put(
            (
                "speech_result",
                text,
                USERNAME
            )
        )

    except sr.UnknownValueError:
        print(
            "Speech could not be understood."
        )

        message_queue.put(
            (
                "speech_failed",
            )
        )

    except sr.RequestError as error:
        print(
            "Speech recognition service error:",
            error
        )

        message_queue.put(
            (
                "speech_failed",
            )
        )

    except Exception as error:
        print(
            "Transcription error:",
            error
        )

        message_queue.put(
            (
                "speech_failed",
            )
        )


# *==============================================================*
# *MICROPHONE / WEBRTC VAD*
# *==============================================================*
def microphone_loop():
    audio = pyaudio.PyAudio()

    stream = None

    try:
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=MIC_CHANNELS,
            rate=MIC_RATE,
            input=True,
            frames_per_buffer=MIC_CHUNK
        )

        vad = webrtcvad.Vad(
            VAD_MODE
        )

        print(
            "Microphone ready."
        )

        speaking = False
        frames = []
        silence_frames = 0

        silence_limit = max(
            1,
            round(
                SILENCE_DURATION
                / (
                    FRAME_MS / 1000
                )
            )
        )

        max_phrase_frames = max(
            1,
            round(
                MAX_PHRASE_SECONDS
                / (
                    FRAME_MS / 1000
                )
            )
        )

        while running:

            # *Prompt focus pauses the microphone.*
            if (
                afk_active.is_set()
                or prompt_focused.is_set()
            ):
                if speaking:
                    speaking = False
                    frames = []
                    silence_frames = 0

                    print(
                        "Speech cancelled (mic paused)."
                    )

                    message_queue.put(
                        (
                            "speech_failed",
                        )
                    )

                time.sleep(
                    0.05
                )

                continue

            frame = stream.read(
                MIC_CHUNK,
                exception_on_overflow=False
            )

            is_speech = vad.is_speech(
                frame,
                MIC_RATE
            )

            # *==================================================*
            # *SPEECH START*
            # *==================================================*
            if (
                is_speech
                and not speaking
            ):
                speaking = True

                frames = [
                    frame
                ]

                silence_frames = 0

                print(
                    "Speech started."
                )

                message_queue.put(
                    (
                        "speech_start",
                    )
                )

                continue

            # *==================================================*
            # *CURRENTLY SPEAKING*
            # *==================================================*
            if speaking:
                frames.append(
                    frame
                )

                if is_speech:
                    silence_frames = 0
                else:
                    silence_frames += 1

                if (
                    silence_frames >= silence_limit
                    or len(frames) >= max_phrase_frames
                ):
                    speaking = False

                    print(
                        "Speech finished."
                    )

                    raw_audio = b"".join(
                        frames
                    )

                    audio_data = sr.AudioData(
                        raw_audio,
                        MIC_RATE,
                        2
                    )

                    threading.Thread(
                        target=transcribe_audio,
                        args=(audio_data,),
                        daemon=True
                    ).start()

                    frames = []
                    silence_frames = 0

    except Exception as error:
        print(
            "Microphone error:",
            error
        )

        message_queue.put(
            (
                "speech_failed",
            )
        )

    finally:
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass

            try:
                stream.close()
            except Exception:
                pass

        audio.terminate()

        print(
            "Microphone stopped."
        )


# *==============================================================*
# *SCRATCH COMMENTS*
# *==============================================================*
comment_http = requests.Session()

COMMENT_URL = (
    "https://api.scratch.mit.edu"
    f"/users/{USERNAME}"
    f"/projects/{PROJECT_ID}"
    "/comments/"
)


def get_latest_comment():
    response = comment_http.get(
        COMMENT_URL,
        params={
            "limit": 40,
            "offset": 0
        },
        timeout=10
    )

    response.raise_for_status()

    comments = response.json()

    if not isinstance(
        comments,
        list
    ):
        return None

    if not comments:
        return None

    def comment_id(comment):
        try:
            return int(
                comment.get(
                    "id",
                    0
                )
            )
        except Exception:
            return 0

    return max(
        comments,
        key=comment_id
    )


def comment_loop():
    last_comment_id = None

    try:
        initial = get_latest_comment()

        if initial is not None:
            last_comment_id = str(
                initial.get(
                    "id",
                    ""
                )
            )

            print(
                "Current latest comment:",
                last_comment_id
            )

    except Exception as error:
        print(
            "Initial comment error:",
            error
        )

    while running:
        if comment_reading_paused.is_set():
            time.sleep(0.05)
            continue

        try:
            latest = get_latest_comment()

            if latest is not None:
                current_id = str(
                    latest.get(
                        "id",
                        ""
                    )
                )

                if (
                    current_id
                    and current_id != last_comment_id
                ):
                    last_comment_id = current_id

                    text = str(
                        latest.get(
                            "content",
                            ""
                        )
                    )

                    author_data = latest.get(
                        "author",
                        {}
                    )

                    if isinstance(
                        author_data,
                        dict
                    ):
                        author = str(
                            author_data.get(
                                "username",
                                ""
                            )
                        )
                    else:
                        author = ""

                    if (
                        text
                        and author
                    ):
                        print(
                            f"New comment from "
                            f"{author}: {text}"
                        )

                        message_queue.put(
                            (
                                "comment",
                                text,
                                author
                            )
                        )

        except Exception as error:
            print(
                "Comment listener error:",
                error
            )

        time.sleep(
            COMMENT_CHECK_INTERVAL
        )


# *==============================================================*
# *CONTROLS*
# *==============================================================*
controls = tk.Frame(
    root
)

controls.pack(
    fill="x"
)


def close():
    global running
    running = False
    root.destroy()


def on_end():
    global running

    # Stop all normal activity immediately.
    running = False

    # Directly send the shutdown value multiple times so Scratch
    # clients have several chances to receive it.
    shutdown_value = compose_info_value(
        "0",
        "0",
        "0"
    )

    for attempt in range(10):
        try:
            message_cloud.set_var(
                INFO_VARIABLE,
                shutdown_value
            )
            print(
                f"INFO shutdown: {shutdown_value} "
                f"({attempt + 1}/10)"
            )
        except Exception as error:
            print(
                f"Shutdown info update failed "
                f"({attempt + 1}/10):",
                error
            )

        if attempt < 9:
            time.sleep(0.1)

    # Now that all 10 updates have been sent, close.
    close()


end_button = tk.Button(
    controls,
    text="End",
    command=on_end
)

end_button.pack(
    side="left",
    padx=4,
    pady=4
)


# *==============================================================*
# *AFK*
# *==============================================================*
afk_var = tk.IntVar(
    value=0
)


def on_toggle_afk():
    is_afk = bool(
        afk_var.get()
    )

    if is_afk:
        afk_active.set()

        set_info_digit(
            1,
            "1",
            immediate=True
        )

    else:
        afk_active.clear()

        set_info_digit(
            1,
            "0",
            immediate=True
        )


afk_toggle = tk.Checkbutton(
    controls,
    text="Go AFK",
    variable=afk_var,
    command=on_toggle_afk
)

afk_toggle.pack(
    side="left",
    padx=4,
    pady=4
)


# *==============================================================*
# *PROMPT*
# *==============================================================*
prompt_var = tk.StringVar()

# *Tracks the previous "has text" state so we don't spam*
# *Scratch with the same info value on every keystroke.*
prompt_has_text = False


def on_prompt_text_changed(*args):
    global prompt_has_text

    # *1 if there is actually text in the prompt,*
    # *0 if the prompt is empty.*
    has_text = bool(
        prompt_var.get().strip()
    )

    # *Only update Scratch when the state actually changes.*
    if has_text == prompt_has_text:
        return

    prompt_has_text = has_text

    message_queue.put(
        (
            "set_prompt_text",
            "1" if has_text else "0"
        )
    )


def on_prompt_focus_in(event):
    # *Focus state is still used ONLY for pausing the microphone.*
    prompt_focused.set()


def on_prompt_focus_out(event):
    # *Focus state is still used ONLY for pausing the microphone.*
    prompt_focused.clear()


def on_submit_prompt(event=None):
    text = prompt_var.get().strip()

    if text:
        message_queue.put(
            (
                "prompt_text",
                text,
                USERNAME
            )
        )

    # *Clearing the prompt automatically triggers*
    # *on_prompt_text_changed(), setting info's last digit to 0.*
    prompt_var.set("")

    # *Enter also removes keyboard focus.*
    if event is not None:
        try:
            canvas.focus_set()
        except Exception:
            root.focus_set()

        return "break"

def on_window_focus_in(event=None):
    # Immediately put keyboard focus into the prompt
    # whenever the Tkinter window becomes active.
    try:
        prompt_entry.focus_set()
        prompt_entry.icursor(tk.END)
    except Exception:
        pass


def on_prompt_focus_in(event):
    # Focus is ONLY used for pausing the microphone.
    prompt_focused.set()


def on_prompt_focus_out(event):
    # Focus is ONLY used for pausing the microphone.
    prompt_focused.clear()

prompt_entry = tk.Entry(
    controls,
    textvariable=prompt_var
)

root.bind("<FocusIn>", on_window_focus_in)

prompt_entry.pack(
    side="left",
    fill="x",
    expand=True,
    padx=4,
    pady=4
)

prompt_entry.bind(
    "<Return>",
    on_submit_prompt
)

prompt_entry.bind(
    "<FocusIn>",
    on_prompt_focus_in
)

prompt_entry.bind(
    "<FocusOut>",
    on_prompt_focus_out
)

# *Watch the actual contents of the Entry.*
prompt_var.trace_add(
    "write",
    on_prompt_text_changed
)

send_button = tk.Button(
    controls,
    text="Send",
    command=on_submit_prompt
)

send_button.pack(
    side="left",
    padx=4,
    pady=4
)

# *==============================================================*
# *BUILD PREVIEW IMAGE*
# *==============================================================*
def build_preview_image(frame, width, height):
    """
    Convert the 0-9 grayscale frame into one PIL image.

    width/height are the dimensions THIS frame was captured at,
    not the (possibly already-changed) global WIDTH/HEIGHT.

    We then scale that image instead of updating 2,408 separate
    Tkinter rectangle objects.
    """

    grayscale_bytes = bytes(
        round(
            int(shade) / 9 * 255
        )
        for shade in frame
    )

    image = Image.frombytes(
        "L",
        (width, height),
        grayscale_bytes
    )

    image = image.resize(
        (
            width * SCALE,
            height * SCALE
        ),
        Image.Resampling.NEAREST
    )

    return image


# *==============================================================*
# *GUI UPDATE*
# *==============================================================*
def gui_update():
    """
    Run the Tkinter-side GUI work on a 60 FPS schedule.

    IMPORTANT:

    This is not making the screen stream 60 FPS.

    The screen stream is still FPS = 10.

    This simply keeps the Tkinter GUI responsive at 60 updates
    per second while avoiding unnecessary preview rendering.
    """

    global preview_photo
    global last_rendered_frame_number

    # *A resolution change happened on a background thread. Only*
    # *the GUI thread is allowed to touch Tkinter widgets, so the*
    # *actual canvas resize happens here.*
    if resolution_change_pending.is_set():
        resolution_change_pending.clear()

        with resolution_lock:
            new_width = WIDTH
            new_height = HEIGHT

        canvas.config(
            width=new_width * SCALE,
            height=new_height * SCALE
        )

        # Force the next frame to redraw even if its frame
        # number hasn't changed yet.
        last_rendered_frame_number = -1

    with frame_lock:
        frame = latest_frame
        frame_width, frame_height = latest_frame_dimensions
        current_frame_number = frame_number

    # *Only rebuild the preview when the actual stream frame*
    # *has changed.*
    if (
        frame is not None
        and current_frame_number != last_rendered_frame_number
    ):
        try:
            preview_image = build_preview_image(
                frame,
                frame_width,
                frame_height
            )

            preview_photo = ImageTk.PhotoImage(
                preview_image
            )

            canvas.itemconfig(
                preview_image_item,
                image=preview_photo
            )

            last_rendered_frame_number = (
                current_frame_number
            )

        except Exception as error:
            print(
                "Preview render error:",
                error
            )

    if latest_rle_length > 0:
        rle_text = (
            f"RLE "
            f"{latest_rle_length}/"
            f"{CLOUD_CAPACITY}"
            f" chars "
            f"({latest_rle_ratio:.2f}×)"
        )
    else:
        rle_text = "RLE: waiting"

    status.config(
        text=(
            "LIVE   "
            f"{frame_width}×{frame_height}"
            f" (level {current_resolution_digit})   "
            f"{frame_width * frame_height} pixels   "
            f"{FPS} FPS   "
            "scr1–scr7   "
            f"{rle_text}   "
            "| MIC + COMMENTS"
        )
    )

    # *Schedule Tkinter GUI processing at 60 FPS.*
    root.after(
        GUI_INTERVAL,
        gui_update
    )


root.protocol(
    "WM_DELETE_WINDOW",
    close
)


# *==============================================================*
# *START THREADS*
# *==============================================================*
capture_thread = threading.Thread(
    target=capture_loop,
    daemon=True
)

screen_thread = threading.Thread(
    target=screen_cloud_loop,
    daemon=True
)

message_thread = threading.Thread(
    target=message_cloud_loop,
    daemon=True
)

microphone_thread = threading.Thread(
    target=microphone_loop,
    daemon=True
)

comment_thread = threading.Thread(
    target=comment_loop,
    daemon=True
)


capture_thread.start()
screen_thread.start()
message_thread.start()
microphone_thread.start()
comment_thread.start()


# *==============================================================*
# *START GUI*
# *==============================================================*
gui_update()

root.mainloop()
