# Saywise

Saywise is a local-first English speaking practice application. It combines Faster-Whisper transcription, reference-versus-transcript word edits, measurable timing features, and an optional wav2vec2 phoneme-recognition experiment. It does not treat Whisper or forced alignment as proof of a pronunciation error, and it does not display an unvalidated pronunciation score.

## Requirements

- Windows 10/11, macOS, or Linux.
- Python 3.11 or 3.12 is the recommended setup for the speech/audio packages. This workspace currently has Python 3.15.2; the app's non-inference routes and unit tests can run there, but the complete audio/model dependency stack has not been verified on Python 3.15.
- Internet access is needed for the initial model-weight download. Inference runs locally after the weights are cached.
- CPU inference is supported. A compatible NVIDIA CUDA/CTranslate2 setup is optional.

## Install and run on Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:SAYWISE_SECRET_KEY = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
.\.venv\Scripts\python.exe app.py
```

Open `http://127.0.0.1:5000`. If the Python launcher does not provide 3.12, install Python 3.12 and use its executable to create the virtual environment. For local-only testing, the app generates a temporary secret automatically; set a persistent secret for any deployment.

The first analysis loads Faster-Whisper's configured model (`small.en` by default), which can take time and disk space. Choose a smaller model such as `base.en` for a lower-resource machine, or set `SAYWISE_ASR_MODEL`. `SAYWISE_DEVICE` and `SAYWISE_COMPUTE_TYPE` select CPU/GPU and precision; defaults are CPU and INT8.

## Optional phone-recognition experiment

The phone panel requires PyTorch, Transformers, Phonemizer, and an installed eSpeak NG executable. Install PyTorch using the wheel appropriate for the machine, then install `requirements-phonemes.txt`. The first run downloads `facebook/wav2vec2-lv-60-espeak-cv-ft`. The checkpoint recognizes phonetic labels; it is **not** a learner-pronunciation scoring model. The output is explicitly marked experimental and has no calibrated confidence or numeric pronunciation score.

If the optional stack or its weights are unavailable, Saywise still performs ASR, word comparison, and timing analysis; the phone panel reports that it is unavailable. The UI uses Google Fonts and Chart.js from CDNs; speech audio is not sent to those providers. Model weights may be fetched from their hosting service on first use.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests cover transcript edit alignment, WER/PER and detection metrics, pause/rate summaries, CSRF, account authentication, password hashing, and protected routes. They do not measure speech-model quality. Do not report AI accuracy until the evaluation protocol in `docs/PROJECT_REPORT.md` has been run against the held-out labeled data.

## Data and administration

SQLite and retained recordings are stored under the Flask `instance` directory. Audio is deleted after analysis by default. Checking “Keep this recording in my history” opts into persistent audio storage; a user can delete a recording with its attempt, clear all practice history, or delete their account and data. The app always stores transcript and assessment metadata as practice history until the user deletes it.

To create the first admin account, set `SAYWISE_ADMIN_EMAIL` before registering that exact email. Do not expose the Flask development server to the public internet. Production deployment needs HTTPS, a persistent secret, a production WSGI server, and additional operational controls.

## Project material

See [`docs/PROJECT_REPORT.md`](docs/PROJECT_REPORT.md) for the architecture, model rationale, database schema, Mermaid DFD/UML diagrams, algorithms, test plan, evaluation strategy, limitations, and references.