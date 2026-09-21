# jev-gmail-labeler

A tool that uses Jev and the GMail API to organize your emails with labels.

This is a work in progress. See [TODO](#todo) below.

## Content

- [jev-gmail-labeler](#jev-gmail-labeler)
  - [Content](#content)
  - [Audience](#audience)
  - [Installation](#installation)
  - [Auth](#auth)
  - [Usage](#usage)
  - [Further Documentation](#further-documentation)
  - [TODO](#todo)
  - [LLM Use Disclosure](#llm-use-disclosure)

## Audience

This is intended primarily for personal use, so it's a little rough around the edges.

## Installation

I have not published this to PyPI. Install it from this repository:

```
uv pip install "git+https://github.com/1npo/jev-gmail-labeler.git"
```

## Auth

Put your TypeSafe API key the `TYPESAFE_API_KEY` environment variable.

## Usage

You need to run a few `jev-gmail-labeler` commands in a sequence.

Currently each command gets its input from a JSON file and saves its output to a JSON file. By default these files come from and are saved to a `workspace/` folder in the current directory.

1. `jev-gmail-labeler get-emails` -> `workspace/email_cache.json`
2. `jev-gmail-labeler anonymize-emails` -> `workspace/anonymized_email_cache.json`
3. `jev-gmail-labeler classify-emails` -> `workspace/classification_cache.json`
4. `jev-gmail-labeler label-emails`

Save `workspace/classification_cache.json` to a CSV file by running `jev-gmail-labeler report`.

## Further Documentation

See [ABOUT_JEV.md](ABOUT_JEV.md) for my summary of what Jev is and how it works.

Not much else besides that. See `jev-gmail-labeler <command> --help` for options you can provide for each command.

## TODO

- [x] Build GMail API helper module
  - [x] Get emails
  - [x] Manage labels
- [x] Implement CLI
- [x] Implement anonymize command
- [x] Implement classify command
- [ ] Implement label command
- [x] Implement report command

## LLM Use Disclosure

* The `gmail_api_util.py` module was vibe-coded
* Claude help refactor a few functions in `main.py`
