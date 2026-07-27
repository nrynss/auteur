"""video + mix WAV -> final mp4 -- T4.

One ffmpeg subprocess: copy the original video stream untouched (picture-lock
claim: -c:v copy, byte-identical video) and swap in the new audio track.
Zero API calls.
"""

import subprocess


def mux(video_path: str, wav_path: str, out_path: str) -> str:
    """ffmpeg -y -i video -i wav -map 0:v -map 1:a -c:v copy -shortest out.

    Video stream is copied untouched (-c:v copy); audio is the rendered mix.
    -shortest trims to the shorter of the two inputs (they're expected to
    match within a few ms since the mix is rendered to the ffprobe duration).
    Raises RuntimeError with ffmpeg's stderr on failure. Returns out_path.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", wav_path,
        "-map", "0:v:0",
        "-map", "1:a",
        "-c:v", "copy",
        "-shortest",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg mux failed ({video_path} + {wav_path} -> {out_path}): {proc.stderr}"
        )
    return out_path


def extract_audio(video_path: str, dest_wav_path: str) -> bool:
    """Attempts to extract audio from video_path as 48kHz stereo float32 WAV.
    Returns True on success, False if no audio stream exists or ffmpeg fails.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-ar", "48000",
        "-ac", "2",
        "-c:a", "pcm_f32le",
        dest_wav_path
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0

