# Universal Filename Translator

A desktop app for bulk-translating folder and file names between any two languages.

Built with Python + Tkinter. Uses the Google Translate free endpoint.

![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue) ![License: MIT](https://img.shields.io/badge/license-MIT-green)

---

## Features

- Translates file and folder names across 30+ languages
- Batches up to 100 names per request
- Preserves file extensions and original folder structure
- Copies files to a new output folder
- Skips names that are already in the target language
- Smart filtering: CJK, Cyrillic, Arabic, and other scripts are only sent when relevant characters are actually present
- Sanitizes translated names for cross-platform compatibility (Windows, macOS, Linux)
- Cancellable mid-run, with a live translation log
- Optional drag & drop support via `tkinterdnd2`

## Performance

Tested on a folder of **10,000 files**, Chinese → English:

- Total time: ~15 minutes
- Throughput: ~11 files/sec (network-bound)

## Requirements

- Python 3.8+
- No third-party packages required for core functionality
- Optional: `tkinterdnd2` for drag & drop support

```
pip install tkinterdnd2
```

## Usage

```
python universal_filename_translator.py
```

1. Select your source and target languages
2. Click the folder area (or drag & drop a folder)
3. Click **Translate Names**
4. A new folder is created next to your original with `_translated` appended to its name

## Supported Languages

Arabic, Chinese (Simplified & Traditional), Czech, Danish, Dutch, English, Finnish, French, German, Greek, Hebrew, Hindi, Hungarian, Indonesian, Italian, Japanese, Korean, Norwegian, Persian, Polish, Portuguese, Romanian, Russian, Spanish, Swedish, Thai, Turkish, Ukrainian, Vietnamese — plus auto-detect as a source option.

## A note on Google Translate

This tool uses Google's unofficial free translation endpoint (`translate.googleapis.com`). No API key or account is needed, but this endpoint is not officially supported by Google and its use technically falls outside their Terms of Service. It works reliably for personal and hobby use — just be aware there are no uptime guarantees, and usage at very high scale may be rate-limited or blocked.

If you need a production-grade solution, swap in the [official Google Cloud Translation API](https://cloud.google.com/translate).
