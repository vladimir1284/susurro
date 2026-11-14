# Getting started

Setting up Susurro end to end: one machine running the models, one capturing audio, and a
phone or second screen showing the hints.

Read [Hardware](../hardware.md) first if you are not sure your machine can run this. The
short version: Apple Silicon with 16 GB, or an x86 box with 8 GB of VRAM. Below that, the
speech model and the language model will not both stay resident.

!!! warning "Nothing here is benchmarked yet"
    These instructions work. Whether they work *fast enough* on your hardware is what
    [M0](../roadmap.md#m0-benchmark) exists to answer, and it has not been run. If hints
    arrive too late to use, that is the thing to measure before changing anything else.

## 1. Try it without any models

Do this first, even if you have the hardware. It takes two minutes, it proves the plumbing
works, and it means a later problem is a model problem rather than a mystery.

```bash
git clone https://github.com/vladimir1284/susurro
cd susurro
python3 -m venv .venv
.venv/bin/pip install -e ".[core]"
.venv/bin/python -m core.main
```

It prints a console URL with a token in it. Open that on the same machine.

There are no profiles yet, so **Start** is disabled. Press **New**, give it a label and
some background text, and press **Save**. Start is now available.

Press **Start**, then **Hint**. Canned text appears. Nothing was transcribed and nothing
was generated — the core ships a scripted recognizer and a canned hint generator so the
console, the protocol and the capture client can be worked on without models. That is the
whole system apart from the two expensive pieces.

## 2. Install the models

```bash
.venv/bin/pip install -e ".[core,whisper,openai,vad]"
.venv/bin/pip install --no-deps silero-vad
```

The last line is separate on purpose. `silero-vad` declares a dependency on torch, and
only the 2 MB ONNX file it ships is used, so installing torch for it would be absurd.

Then start a language model server. Either of these speaks the API Susurro expects:

```bash
llama-server -m <model>.gguf -c 8192 --port 8080     # llama.cpp
mlx_lm.server --model <repo> --port 8080             # MLX, Apple Silicon
```

An 8B model at 4-bit quantization is the ceiling on a 16 GB machine — see
[Inference](../architecture/inference.md#the-binding-constraint-is-memory) for why.

## 3. Configure the core

```bash
cp core.toml.example core.toml
python3 -c "import secrets; print(secrets.token_urlsafe(16))"   # a token
```

Edit `core.toml`: set the token, set `[stt] backend = "whisper"` and
`[llm] backend = "openai"`, and check `[llm] base_url` matches the server you started.

Set `[stt] language` to the language you will actually be speaking. A session is in one
language from start to finish — see [Audio pipeline](../architecture/audio.md#one-language-per-session).

Leave `[session] record = false` unless you want transcripts written to disk. It is off by
default deliberately.

```bash
.venv/bin/python -m core.main --config core.toml
```

The first run downloads the speech model, which takes a few minutes and says nothing while
it does. That is normal.

## 4. Capture audio

On the machine hosting the meeting — **not** the one running the models, if you can avoid
it. 16 GB is enough for the models or for a video call, not comfortably for both.

=== "Linux"

    ```bash
    .venv/bin/python -m capture.main --list-devices
    .venv/bin/python -m capture.main \
      --core ws://<core-host>:8723 --token "$SUSURRO_TOKEN" \
      --mic <your microphone node> \
      --far <the monitor of your output sink>
    ```

    The `far` lane is the **monitor** of the sink your meeting app plays through. That is
    the stream carrying everyone else's voice.

=== "macOS"

    macOS has no system audio capture of its own. Install
    [BlackHole](https://github.com/ExistentialAudio/BlackHole), set it as the output your
    meeting app uses, and pass it as `--far`.

=== "No audio devices"

    ```bash
    .venv/bin/python -m capture.main \
      --core ws://localhost:8723 --token "$SUSURRO_TOKEN" \
      --replay-mic me.wav --replay-far them.wav
    ```

    16 kHz mono 16-bit WAV. Useful for testing, for machines with no sound hardware, and
    for reproducing a transcription problem against the same audio twice.

The console's `mic` and `far` indicators turn green as each lane connects.

## 5. Open the console somewhere else

`http://<core-host>:8723/?token=…` on a phone or a second monitor.

Somewhere else is the point. Hints on the screen you are sharing are visible to everyone
in the call.

Pick a profile, press **Start**, and talk. Transcription appears in two lanes. A hint
starts writing on its own when the other party stops talking, or immediately when you
press **Space**.

## When it does not work

| Symptom | Likely cause |
|---|---|
| Console will not connect | Token mismatch, or the core bound to loopback and you are on another machine. It refuses `0.0.0.0` without `--allow-public-bind`. |
| A lane never turns green | The capture client exited. Run it in the foreground and read its error. |
| `lane_busy` | Something is already connected on that lane — usually a capture client you forgot to stop. |
| Transcript is empty but lanes are up | Wrong `far` device: you are capturing silence. `--list-devices` and try the other monitor. |
| Transcript lags further and further behind | The speech model is slower than real time on this hardware. Try a smaller one, and run the benchmark. |
| Hints take many seconds | Almost certainly prefill. See [Inference](../architecture/inference.md#prompt-cache-is-a-requirement-not-an-optimization). |
| Hints are in the wrong language | The profile's language, not the core's. A session takes it from the profile. |
| No hint when the other party stops | `[vad] backend = "off"`, or `silero-vad` is not installed and the energy fallback is not hearing them. |

## What this does not do

Susurro will not hide itself from screen sharing, evade proctoring software, or avoid
detection. The console runs on a second device because hints should not be in your screen
share — a consequence of the design, not a stealth feature.
