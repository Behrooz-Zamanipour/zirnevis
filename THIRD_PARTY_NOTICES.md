# Third-party software and models

The MIT license at the root applies to the Zirnevis application source. It does
not relicense third-party packages, downloaded binaries, or model weights.
Dependencies and model weights are fetched separately during installation.

## Models

- **Whisper small:** OpenAI's Whisper, used through SYSTRAN/faster-whisper.
  [Whisper source and MIT license](https://github.com/openai/whisper),
  [converted weights](https://huggingface.co/Systran/faster-whisper-small).
- **NLLB-200 distilled 600M:** AI at Meta / NLLB Team, 2022.
  [Original model card](https://huggingface.co/facebook/nllb-200-distilled-600M),
  [paper](https://arxiv.org/abs/2207.04672),
  [CC BY-NC 4.0 license](https://creativecommons.org/licenses/by-nc/4.0/).
  The downloaded conversion is an int8 CTranslate2 conversion by JustFrederik,
  pinned to revision `302d78f00e6fdb50a1064059df7c392b735e9d05`:
  [conversion](https://huggingface.co/JustFrederik/nllb-200-distilled-600M-ct2-int8).
  Changes from the original are conversion and quantization. The noncommercial
  model terms remain applicable. Model files are not part of this repository.

See also [MODEL-NOTICES.txt](MODEL-NOTICES.txt).

## Software dependencies

Consult each package's distributed license for the installed version:

| Component | Upstream |
| --- | --- |
| Python | https://www.python.org/psf/license/ |
| FastAPI | https://github.com/fastapi/fastapi |
| Uvicorn | https://github.com/Kludex/uvicorn |
| faster-whisper | https://github.com/SYSTRAN/faster-whisper |
| CTranslate2 | https://github.com/OpenNMT/CTranslate2 |
| PyAV | https://github.com/PyAV-Org/PyAV |
| SentencePiece | https://github.com/google/sentencepiece |
| yt-dlp and optional components | https://github.com/yt-dlp/yt-dlp |
| imageio-ffmpeg | https://github.com/imageio/imageio-ffmpeg |
| FFmpeg / libass | https://ffmpeg.org/legal.html ; https://github.com/libass/libass |
| Requests | https://github.com/psf/requests |
| filelock | https://github.com/tox-dev/filelock |
| Send2Trash | https://github.com/arsenetar/send2trash |
| pywin32 | https://github.com/mhammond/pywin32 |
| Node.js, optional external installation | https://github.com/nodejs/node |

FFmpeg build configuration affects its license and enabled codecs. This source
repository does not bundle an FFmpeg, Node.js, or Python executable. If you
redistribute binaries or installed environments, include their applicable
licenses and satisfy their redistribution requirements separately.
