"""Speech-to-text worker for the Mirror companion.

Stays running with the model loaded. Reads one JSON object per line on
standard input and writes one per line on standard output:

  in:   {"id": "7", "pcm": "<base64 of 16 kHz mono 16-bit samples>"}
  out:  {"id": "7", "text": "go to sleep", "ms": 412, "device": "cuda"}
  or:   {"id": "7", "error": "what went wrong"}

Once at the start it writes {"event": "ready", "device": ..., "detail": ...},
or {"event": "fatal", "detail": ...} before it exits.

Arguments: MODEL DEVICE MODELS_DIR, where DEVICE is auto, cuda or cpu.
"""

import base64
import json
import sys
import time

PROMPT = "The speaker talks to a smart mirror called Mirror."


def write(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def open_model(name, device, models_dir):
    from faster_whisper import WhisperModel

    compute = "float16" if device == "cuda" else "int8"
    try:
        # A model that is already on disk loads without asking the network.
        model = WhisperModel(
            name, device=device, compute_type=compute, download_root=models_dir, local_files_only=True
        )
    except Exception:
        model = WhisperModel(name, device=device, compute_type=compute, download_root=models_dir)
    # The GPU libraries are loaded only when sound is first decoded, so a
    # broken installation shows here and not at the first request.
    transcribe(model, b"\x00\x00" * 16000)
    return model


def transcribe(model, pcm):
    import numpy

    # The samples are handed over as an array: the library's own file
    # decoder (PyAV) does not work on the server this was built for.
    sound = numpy.frombuffer(pcm, dtype="<i2").astype(numpy.float32) / 32768.0
    segments, _ = model.transcribe(
        sound,
        language="en",
        beam_size=1,
        without_timestamps=True,
        condition_on_previous_text=False,
        initial_prompt=PROMPT,
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


def load(name, wanted, models_dir):
    """Returns (model, device, detail). The GPU is tried first unless the CPU was asked for."""
    if wanted != "cpu":
        try:
            return open_model(name, "cuda", models_dir), "cuda", ""
        except Exception as error:
            if wanted == "cuda":
                raise
            detail = "The GPU could not be used (%s), so speech is recognised on the CPU, which is slower." % one_line(error)
            return open_model(name, "cpu", models_dir), "cpu", detail
    return open_model(name, "cpu", models_dir), "cpu", ""


def one_line(error):
    return " ".join(str(error).split())[:300] or type(error).__name__


def main():
    name, wanted, models_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    started = time.monotonic()
    try:
        model, device, detail = load(name, wanted, models_dir)
    except Exception as error:
        write({"event": "fatal", "detail": "The speech model %s could not be loaded: %s" % (name, one_line(error))})
        return 1
    write({"event": "ready", "device": device, "detail": detail, "loadMs": round((time.monotonic() - started) * 1000)})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request["id"]
            pcm = base64.b64decode(request["pcm"])
            began = time.monotonic()
            try:
                text = transcribe(model, pcm)
            except Exception as error:
                if device != "cuda" or wanted == "cuda":
                    raise
                # A GPU that fails after loading, for example for lack of
                # memory, is given up for the CPU until the worker restarts.
                detail = "The GPU failed (%s), so speech is recognised on the CPU, which is slower." % one_line(error)
                model, device = open_model(name, "cpu", models_dir), "cpu"
                write({"event": "ready", "device": device, "detail": detail, "loadMs": 0})
                text = transcribe(model, pcm)
            write({"id": request_id, "text": text, "ms": round((time.monotonic() - began) * 1000), "device": device})
        except Exception as error:
            write({"id": request_id, "error": one_line(error)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
