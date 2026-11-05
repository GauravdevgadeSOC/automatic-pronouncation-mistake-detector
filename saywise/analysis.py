"""Local speech analysis with explicit limits on pronunciation claims."""

from __future__ import annotations

import os
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from .scoring import compare_words, fluency_summary, tokenize


SAMPLE_RATE = 16_000
PHONE_MODEL_ID = "facebook/wav2vec2-lv-60-espeak-cv-ft"


class AnalysisUnavailable(RuntimeError):
    """A required local inference dependency or model could not be loaded."""


def decode_audio(path: Path, max_duration: int) -> tuple[Any, int, float]:
    try:
        import av
        import numpy as np
    except ImportError as exc:
        raise AnalysisUnavailable("Audio decoding requires the av and numpy packages.") from exc

    try:
        container = av.open(str(path))
        stream = next((item for item in container.streams if item.type == "audio"), None)
        if stream is None:
            raise ValueError("The uploaded file does not contain an audio track.")
        if stream.duration and stream.time_base:
            declared_duration = float(stream.duration * stream.time_base)
            if declared_duration > max_duration:
                raise ValueError(f"Recording must be {max_duration} seconds or shorter.")
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=SAMPLE_RATE)
        chunks: list[np.ndarray] = []
        sample_count = 0
        for frame in container.decode(stream):
            converted = resampler.resample(frame) or []
            for output_frame in converted:
                chunk = output_frame.to_ndarray().reshape(-1).astype(np.float32, copy=False)
                chunks.append(chunk)
                sample_count += len(chunk)
                if sample_count > max_duration * SAMPLE_RATE:
                    raise ValueError(f"Recording must be {max_duration} seconds or shorter.")
        tail = resampler.resample(None) or []
        for output_frame in tail:
            chunk = output_frame.to_ndarray().reshape(-1).astype(np.float32, copy=False)
            chunks.append(chunk)
        container.close()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The audio could not be decoded. Try recording again in your browser.") from exc

    if not chunks:
        raise ValueError("The recording contains no decodable audio.")
    samples = np.concatenate(chunks)
    duration = len(samples) / SAMPLE_RATE
    if duration < 1:
        raise ValueError("Record at least one second of speech.")
    if duration > max_duration:
        raise ValueError(f"Recording must be {max_duration} seconds or shorter.")
    if float(np.max(np.abs(samples))) < 0.003:
        raise ValueError("No clear speech signal was detected. Check your microphone and try again.")
    return samples, SAMPLE_RATE, duration


def analyze_recording(
    path: Path,
    samples: Any,
    sample_rate: int,
    duration: float,
    expected_text: str,
) -> dict[str, Any]:
    segments, language_probability = _transcribe(path)
    transcript = " ".join(segment["text"].strip() for segment in segments).strip()
    comparison = compare_words(expected_text, transcript)
    speech_seconds, pauses = _energy_intervals(samples, sample_rate, duration)
    fluency = fluency_summary(
        duration, speech_seconds, pauses, len(tokenize(transcript))
    )
    try:
        phonemes = _compare_phonemes(samples, sample_rate, expected_text)
    except AnalysisUnavailable as exc:
        phonemes = {"status": "unavailable", "reason": str(exc)}

    feedback: list[str] = []
    if comparison["edits"]:
        feedback.append(
            "Compare the recognized words with the prompt, then repeat the full sentence slowly. "
            "A transcript mismatch can also be an ASR error."
        )
    else:
        feedback.append("The transcript matches the prompt. Try one more steady repetition to build consistency.")
    if fluency["longest_pause_seconds"] >= 1.2:
        feedback.append("There was a long pause. Practice in short phrases, then join the phrases together.")
    if phonemes["status"] == "experimental" and phonemes.get("alignment"):
        feedback.append(
            "The experimental phone recognizer found sequence differences. Treat these as review cues, "
            "not confirmed pronunciation mistakes."
        )

    return {
        "transcript": transcript,
        "language_confidence": round(language_probability, 3),
        "words": [word for segment in segments for word in segment["words"]],
        "word_comparison": comparison,
        "fluency": fluency,
        "pronunciation": {"status": "not_calibrated", "score": None},
        "overall_performance": {"status": "not_calibrated", "score": None},
        "phonemes": phonemes,
        "feedback": feedback,
        "methodology_note": (
            "Whisper transcript matching is lexical, not phonetic. Phoneme results are experimental; "
            "no pronunciation score is shown until validated against human-labeled data."
        ),
    }


@lru_cache(maxsize=1)
def _whisper_model() -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise AnalysisUnavailable(
            "Speech recognition is not installed. Install requirements.txt, then restart Saywise."
        ) from exc
    model_name = os.environ.get("SAYWISE_ASR_MODEL", "small.en")
    device = os.environ.get("SAYWISE_DEVICE", "cpu")
    compute_type = os.environ.get("SAYWISE_COMPUTE_TYPE", "int8" if device == "cpu" else "float16")
    try:
        return WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as exc:
        raise AnalysisUnavailable(
            f"Could not load the local speech model '{model_name}'. Check model access and device settings."
        ) from exc


def _transcribe(path: Path) -> tuple[list[dict[str, Any]], float]:
    try:
        model = _whisper_model()
        raw_segments, info = model.transcribe(
            str(path), language="en", word_timestamps=True, vad_filter=True
        )
        segments = list(raw_segments)
    except AnalysisUnavailable:
        raise
    except Exception as exc:
        raise AnalysisUnavailable("Local transcription failed. Try a clearer recording.") from exc

    normalized: list[dict[str, Any]] = []
    for segment in segments:
        normalized.append({
            "text": segment.text,
            "words": [
                {
                    "text": word.word.strip(),
                    "start": round(float(word.start), 2),
                    "end": round(float(word.end), 2),
                    "probability": round(float(word.probability), 3),
                }
                for word in (segment.words or [])
            ],
        })
    return normalized, float(info.language_probability)


def _energy_intervals(
    samples: Any, sample_rate: int, duration: float
) -> tuple[float, list[float]]:
    import numpy as np

    frame_size = max(1, int(sample_rate * 0.02))
    usable = samples[: len(samples) // frame_size * frame_size]
    if len(usable) == 0:
        return 0.0, []
    frames = usable.reshape(-1, frame_size)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    threshold = max(0.008, float(np.max(rms)) * 0.12)
    active = rms >= threshold
    active_indices = np.flatnonzero(active)
    if len(active_indices) == 0:
        return 0.0, []

    groups: list[tuple[int, int]] = []
    start = previous = int(active_indices[0])
    merge_frames = 10
    for raw_index in active_indices[1:]:
        index = int(raw_index)
        if index - previous > merge_frames:
            groups.append((start, previous + 1))
            start = index
        previous = index
    groups.append((start, previous + 1))

    speech_seconds = sum((end - start) * frame_size / sample_rate for start, end in groups)
    pauses = [
        round((next_start - end) * frame_size / sample_rate, 3)
        for (_, end), (next_start, _) in zip(groups, groups[1:])
        if (next_start - end) * frame_size / sample_rate >= 0.25
    ]
    return min(speech_seconds, duration), pauses


def _compare_phonemes(samples: Any, sample_rate: int, expected_text: str) -> dict[str, Any]:
    try:
        import torch
        from phonemizer import phonemize
        from transformers import AutoModelForCTC, AutoProcessor
    except ImportError as exc:
        raise AnalysisUnavailable(
            "Optional phoneme analysis is not installed. See the phoneme setup notes in README.md."
        ) from exc

    try:
        expected = phonemize(
            expected_text,
            language="en-us",
            backend="espeak",
            strip=True,
            preserve_punctuation=False,
            with_stress=True,
        )
        processor, model = _phone_model(AutoProcessor, AutoModelForCTC)
        encoded = processor(samples, sampling_rate=sample_rate, return_tensors="pt")
        with torch.inference_mode():
            logits = model(encoded.input_values).logits
        recognized = processor.batch_decode(logits.argmax(dim=-1))[0].strip()
    except Exception as exc:
        raise AnalysisUnavailable(
            "The optional phoneme model could not analyze this clip. Check its weights and eSpeak installation."
        ) from exc

    expected_tokens = expected.split()
    recognized_tokens = recognized.split()
    matcher = SequenceMatcher(a=expected_tokens, b=recognized_tokens, autojunk=False)
    alignment = [
        {
            "operation": tag,
            "expected": expected_tokens[a_start:a_end],
            "recognized": recognized_tokens[b_start:b_end],
        }
        for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes()
        if tag != "equal"
    ]
    return {
        "status": "experimental",
        "model": PHONE_MODEL_ID,
        "expected": expected,
        "recognized": recognized,
        "alignment": alignment,
        "confidence": None,
        "notice": "Phone sequence comparison is not a calibrated pronunciation assessment.",
    }


@lru_cache(maxsize=1)
def _phone_model(processor_class: Any, model_class: Any) -> tuple[Any, Any]:
    try:
        processor = processor_class.from_pretrained(PHONE_MODEL_ID)
        model = model_class.from_pretrained(PHONE_MODEL_ID)
        model.eval()
        return processor, model
    except Exception as exc:
        raise AnalysisUnavailable(
            "Could not load the optional phoneme model. Its weights may require a first-run download."
        ) from exc