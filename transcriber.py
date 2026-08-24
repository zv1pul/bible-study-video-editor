"""
transcriber.py
==============
Step 1: pull the audio out of the recording and turn it into a timestamped
transcript.

Two interchangeable back ends, both free:

    "local"  -> faster-whisper running on this machine (no API key, no upload,
                works offline; speed depends on the computer).
    "groq"   -> Groq's hosted Whisper large-v3-turbo (free tier, very fast,
                needs an API key; good for low-powered laptops and for the
                web-hosted version of the app).

Nothing here imports Streamlit, so the module can be unit tested or driven
from a plain script.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import tempfile
from typing import Callable, Dict, List, Optional, Sequence

import requests

# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


class Segment(dict):
    """
    One chunk of speech: ``{"start": float, "end": float, "text": str}``.

    This really is a dictionary — ``isinstance(segment, dict)`` is True, it
    serialises straight to JSON, and ``segment["start"]`` works as expected.
    It also exposes ``segment.start`` for readability, which is how the rest
    of the app reads it.

    When word timestamps are switched on, a ``"words"`` key is added holding
    ``[{"start", "end", "word"}, ...]`` for finer-grained alignment.
    """

    def __init__(self, start: float, end: float, text: str, words=None):
        super().__init__(start=float(start), end=float(end), text=str(text))
        if words:
            self["words"] = words

    @property
    def start(self) -> float:
        return self["start"]

    @property
    def end(self) -> float:
        return self["end"]

    @property
    def text(self) -> str:
        return self["text"]

    @property
    def words(self) -> list:
        return self.get("words", [])

    def to_dict(self) -> dict:
        return dict(self)


ProgressCallback = Optional[Callable[[float, str], None]]

LOCAL_MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3"]
GROQ_TRANSCRIBE_MODEL = "whisper-large-v3-turbo"
GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

DEFAULT_AUDIO_PATH = "temp_audio.wav"


def _report(cb: ProgressCallback, fraction: float, message: str) -> None:
    if cb is not None:
        try:
            cb(max(0.0, min(1.0, fraction)), message)
        except Exception:  # a broken UI callback must never kill the pipeline
            pass


# --------------------------------------------------------------------------
# FFmpeg plumbing
# --------------------------------------------------------------------------


def ffmpeg_exe() -> str:
    """
    Locate an ffmpeg binary.

    Order of preference:
      1. FFMPEG_BINARY environment variable
      2. the copy bundled with the `imageio-ffmpeg` pip package  <- the usual
         case, which is why users do not need to install FFmpeg by hand
      3. ffmpeg on the system PATH
    """
    env_path = os.environ.get("FFMPEG_BINARY")
    if env_path and os.path.exists(env_path):
        return env_path

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass

    found = shutil.which("ffmpeg")
    if found:
        return found

    raise RuntimeError(
        "FFmpeg was not found. Install the Python package `imageio-ffmpeg` "
        "(pip install imageio-ffmpeg) or install FFmpeg system-wide."
    )


def _run(command: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )


def _probe(media_path: str) -> str:
    return _run([ffmpeg_exe(), "-hide_banner", "-i", media_path]).stderr or ""


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def probe_duration(media_path: str) -> float:
    """Length of a media file in seconds (parsed out of ffmpeg's banner)."""
    match = _DURATION_RE.search(_probe(media_path))
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def has_audio_track(media_path: str) -> bool:
    return "Audio:" in _probe(media_path)


def has_video_track(media_path: str) -> bool:
    """True for a video file, False for a bare audio file."""
    return "Video:" in _probe(media_path)


# --------------------------------------------------------------------------
# Silence detection
# --------------------------------------------------------------------------

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")


def detect_silences(
    media_path: str,
    min_seconds: float = 2.0,
    noise_db: int = -32,
) -> List[Dict[str, float]]:
    """
    Find the gaps where nobody is speaking.

    A language model reads words; it cannot hear a pause. Reflection time
    after an application question is a pause, so it has to be measured from
    the audio itself rather than inferred from the transcript.

    Returns ``[{"start": float, "end": float, "duration": float}, ...]``
    sorted by start time.
    """
    result = _run([
        ffmpeg_exe(), "-hide_banner", "-nostats",
        "-i", media_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_seconds}",
        "-f", "null", "-",
    ])
    text = result.stderr or ""

    silences: List[Dict[str, float]] = []
    pending: Optional[float] = None
    for line in text.splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match:
            pending = max(float(start_match.group(1)), 0.0)
        end_match = _SILENCE_END_RE.search(line)
        if end_match:
            end = float(end_match.group(1))
            duration = float(end_match.group(2))
            start = pending if pending is not None else max(end - duration, 0.0)
            silences.append({
                "start": round(start, 2),
                "end": round(end, 2),
                "duration": round(duration, 2),
            })
            pending = None

    silences.sort(key=lambda item: item["start"])
    return silences


def find_silence_after(
    silences: Sequence[Dict[str, float]],
    after: float,
    within: float = 12.0,
    min_duration: float = 15.0,
) -> Optional[Dict[str, float]]:
    """
    The reflection pause belonging to a question that finishes at `after`.

    Looks for a long enough silence beginning within `within` seconds of that
    moment — long enough to be deliberate thinking time, close enough to
    belong to this question rather than the next thing in the lesson.
    """
    best = None
    for silence in silences:
        if silence["duration"] < min_duration:
            continue
        gap = silence["start"] - after
        if -2.0 <= gap <= within:
            if best is None or silence["start"] < best["start"]:
                best = silence
    return best


# --------------------------------------------------------------------------
# Step 1a: audio extraction
# --------------------------------------------------------------------------


def extract_audio(
    video_path: str,
    output_audio_path: str = DEFAULT_AUDIO_PATH,
    *,
    for_api: bool = False,
    sample_rate: int = 16000,
    progress_cb: ProgressCallback = None,
) -> str:
    """
    Extract the audio track from a video file with FFmpeg.

        extract_audio("lesson.mp4")                     -> "temp_audio.wav"
        extract_audio("lesson.mp4", "/tmp/lesson.wav")  -> "/tmp/lesson.wav"

    Whisper only ever needs 16 kHz mono, so the track is downmixed on the way
    out: smaller file, faster transcription, no loss of accuracy.

    for_api=False -> 16 kHz mono WAV  (what faster-whisper wants)
    for_api=True  -> 16 kHz mono OGG/Opus, which keeps even a two-hour
                     recording under the hosted API's upload limit

    Returns the path actually written.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"No such file: {video_path}")
    if not has_audio_track(video_path):
        raise RuntimeError(
            "This video has no audio track, so there is nothing to transcribe."
        )

    output_audio_path = output_audio_path or DEFAULT_AUDIO_PATH
    wanted = ".ogg" if for_api else ".wav"
    if not os.path.splitext(output_audio_path)[1]:
        output_audio_path += wanted

    folder = os.path.dirname(os.path.abspath(output_audio_path))
    os.makedirs(folder, exist_ok=True)

    _report(progress_cb, 0.05, "Extracting the audio track…")

    codec_args = (
        ["-c:a", "libopus", "-b:a", "24k", "-application", "voip"]
        if for_api
        else ["-c:a", "pcm_s16le"]
    )

    result = _run([
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-vn",                      # drop the picture
        "-ac", "1",                 # mono
        "-ar", str(sample_rate),    # 16 kHz
        *codec_args,
        output_audio_path,
    ])

    if result.returncode != 0 or not os.path.exists(output_audio_path):
        # Fall back to MoviePy in case the direct call hit an odd container.
        try:
            from moviepy import VideoFileClip

            with VideoFileClip(video_path) as clip:
                if clip.audio is None:
                    raise RuntimeError("no audio stream")
                wav_path = os.path.splitext(output_audio_path)[0] + ".wav"
                clip.audio.write_audiofile(
                    wav_path, fps=sample_rate, nbytes=2,
                    codec="pcm_s16le", logger=None,
                )
                return wav_path
        except Exception as exc:  # pragma: no cover - depends on input file
            raise RuntimeError(
                f"Could not extract audio from the video.\n{result.stderr}\n{exc}"
            ) from exc

    _report(progress_cb, 0.12, "Audio extracted.")
    return output_audio_path


def _split_audio(audio_path: str, chunk_seconds: int, workdir: str) -> List[str]:
    """Cut a long audio file into fixed-length pieces for the hosted API."""
    pattern = os.path.join(workdir, "chunk_%04d.ogg")
    result = _run([
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", audio_path,
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-c", "copy",
        pattern,
    ])
    chunks = sorted(
        os.path.join(workdir, name)
        for name in os.listdir(workdir)
        if name.startswith("chunk_")
    )
    if result.returncode != 0 or not chunks:
        return [audio_path]
    return chunks


# --------------------------------------------------------------------------
# Step 1b: transcription
# --------------------------------------------------------------------------

# One model at a time, guarded by a lock.
#
# Two things go wrong on a shared server otherwise: a user switching from
# "base" to "large-v3" leaves both resident (about 3 GB between them), and two
# people transcribing at once drive the same model object concurrently, which
# faster-whisper does not promise to survive.
_MODEL_CACHE: dict = {}
_MODEL_LOCK = threading.Lock()
_TRANSCRIBE_LOCK = threading.Lock()

try:
    import faster_whisper as _fw  # noqa: F401

    LOCAL_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the install
    LOCAL_AVAILABLE = False


def _load_local_model(model_size: str, compute_type: str = "int8"):
    key = (model_size, compute_type)
    with _MODEL_LOCK:
        if key not in _MODEL_CACHE:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "faster-whisper is not installed. Either run "
                    "`pip install faster-whisper` or switch the transcription "
                    "engine to the hosted (Groq) option."
                ) from exc
            # Drop any other size first: keeping "base" resident while
            # "large-v3" loads is how a 16 GB container runs out of memory.
            _MODEL_CACHE.clear()
            # CPU + int8 is the portable choice: identical behaviour on Mac,
            # Windows and Linux, no GPU or CUDA install required.
            _MODEL_CACHE[key] = WhisperModel(
                model_size, device="cpu", compute_type=compute_type
            )
        return _MODEL_CACHE[key]


def _transcribe_local(
    audio_path: str,
    model_size: str,
    language: Optional[str],
    word_timestamps: bool,
    progress_cb: ProgressCallback,
) -> List[Segment]:
    _report(progress_cb, 0.15, f"Loading the '{model_size}' speech model…")
    model = _load_local_model(model_size)

    total = probe_duration(audio_path) or 0.0
    _report(progress_cb, 0.2, "Transcribing…")

    # One transcription at a time: the model object is shared.
    with _TRANSCRIBE_LOCK:
        return _decode(model, audio_path, language, word_timestamps,
                       progress_cb, total)


def _decode(model, audio_path, language, word_timestamps, progress_cb, total):
    raw_segments, _info = model.transcribe(
        audio_path,
        beam_size=1,                     # greedy decoding: faster, ample accuracy
        vad_filter=True,                 # skip silence, avoids invented text
        language=language or None,
        condition_on_previous_text=False,
        word_timestamps=word_timestamps,
    )

    results: List[Segment] = []
    for segment in raw_segments:  # this generator is what does the work
        text = (segment.text or "").strip()
        if not text:
            continue

        words = None
        if word_timestamps and getattr(segment, "words", None):
            words = [
                {
                    "start": float(w.start),
                    "end": float(w.end),
                    "word": (w.word or "").strip(),
                }
                for w in segment.words
                if w.start is not None and w.end is not None
            ]

        results.append(Segment(segment.start, segment.end, text, words))

        if total:
            done = 0.2 + 0.75 * min(float(segment.end) / total, 1.0)
            _report(
                progress_cb, done,
                f"Transcribing… {format_timestamp(segment.end)} / "
                f"{format_timestamp(total)}",
            )
    return results


def _transcribe_groq(
    audio_path: str,
    api_key: str,
    language: Optional[str],
    word_timestamps: bool,
    progress_cb: ProgressCallback,
) -> List[Segment]:
    if not api_key:
        raise RuntimeError("A Groq API key is required for hosted transcription.")

    workdir = tempfile.mkdtemp(prefix="bsve_chunks_")
    try:
        chunk_seconds = 900  # 15 minutes per request keeps us under the limits
        chunks = _split_audio(audio_path, chunk_seconds, workdir)
        results: List[Segment] = []

        granularities = ["segment"] + (["word"] if word_timestamps else [])

        for index, chunk in enumerate(chunks):
            offset = index * chunk_seconds
            _report(
                progress_cb,
                0.15 + 0.8 * (index / max(len(chunks), 1)),
                f"Transcribing part {index + 1} of {len(chunks)}…",
            )
            with open(chunk, "rb") as handle:
                form: List[tuple] = [
                    ("model", GROQ_TRANSCRIBE_MODEL),
                    ("response_format", "verbose_json"),
                    ("temperature", "0"),
                ]
                for level in granularities:
                    form.append(("timestamp_granularities[]", level))
                if language:
                    form.append(("language", language))

                response = requests.post(
                    GROQ_TRANSCRIBE_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (os.path.basename(chunk), handle, "audio/ogg")},
                    data=form,
                    timeout=600,
                )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Hosted transcription failed ({response.status_code}): "
                    f"{response.text[:400]}"
                )

            payload = response.json()
            words_all = payload.get("words") or []
            for segment in payload.get("segments", []) or []:
                text = (segment.get("text") or "").strip()
                if not text:
                    continue
                start = float(segment.get("start", 0.0))
                end = float(segment.get("end", 0.0))
                words = [
                    {
                        "start": float(w.get("start", 0.0)) + offset,
                        "end": float(w.get("end", 0.0)) + offset,
                        "word": (w.get("word") or "").strip(),
                    }
                    for w in words_all
                    if start <= float(w.get("start", -1)) <= end
                ] or None
                results.append(Segment(start + offset, end + offset, text, words))
        return results
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def transcribe_video(
    audio_path: str,
    model_size: str = "base",
    *,
    engine: str = "local",
    api_key: str = "",
    language: Optional[str] = "en",
    word_timestamps: bool = True,
    progress_cb: ProgressCallback = None,
    keep_audio: bool = False,
) -> List[Segment]:
    """
    Transcribe a recording and return timestamped segments.

        transcribe_video("temp_audio.wav")            # an audio file
        transcribe_video("lesson.mp4")                # a video works too
        transcribe_video("lesson.mp4", "small")

    `audio_path` may be either an audio file or a video: if it still has a
    picture, the audio is extracted to a scratch file first and cleaned up
    afterwards (pass keep_audio=True to keep it).

    Returns a list of dictionaries, in video order:

        [{"start": 12.5, "end": 18.2, "text": "Good morning…"}, ...]

    With word_timestamps on (the default) each segment also carries a "words"
    list of per-word timings.
    """
    engine = (engine or "local").lower()

    working_path = audio_path
    scratch: Optional[str] = None
    if has_video_track(audio_path):
        handle, scratch = tempfile.mkstemp(
            suffix=".ogg" if engine == "groq" else ".wav", prefix="bsve_audio_"
        )
        os.close(handle)
        working_path = extract_audio(
            audio_path, scratch, for_api=(engine == "groq"), progress_cb=progress_cb
        )
        scratch = working_path

    try:
        if engine == "groq":
            segments = _transcribe_groq(
                working_path, api_key, language, word_timestamps, progress_cb
            )
        else:
            segments = _transcribe_local(
                working_path, model_size, language, word_timestamps, progress_cb
            )
    finally:
        if scratch and not keep_audio:
            try:
                os.remove(scratch)
            except OSError:
                pass

    segments.sort(key=lambda s: s["start"])
    _report(progress_cb, 1.0, f"Transcript ready — {len(segments)} segments.")
    return segments


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------


def format_stamp(seconds: float) -> str:
    """`MM:SS.ms` — 83.4 seconds becomes "01:23.400"."""
    seconds = max(0.0, float(seconds or 0.0))
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:06.3f}"


def format_transcript(segments: Sequence[Segment]) -> str:
    """
    The readable, timestamped transcript handed to the language model:

        [00:00.000 -> 00:06.240] Good morning, and welcome.
        [00:06.240 -> 00:14.100] Please open with me to Genesis chapter four.
    """
    return "\n".join(
        f"[{format_stamp(s['start'])} -> {format_stamp(s['end'])}] {s['text'].strip()}"
        for s in segments
        if str(s.get("text", "")).strip()
    )


def format_timestamp(seconds: float) -> str:
    """`M:SS` or `H:MM:SS` — the short form used in the interface."""
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def segments_to_text(segments: Sequence[Segment]) -> str:
    """Plain transcript for the download button."""
    return "\n".join(
        f"[{format_timestamp(s['start'])}] {s['text']}" for s in segments
    )


def segments_to_json(segments: Sequence[Segment]) -> str:
    return json.dumps([dict(s) for s in segments], indent=2)
