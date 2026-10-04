You are planning a complete redesign and rewrite of this application. **Do not implement anything.** Your only deliverable is a written implementation plan, saved to `planning/PLAN.md` in the project (create `planning/` if needed) and committed on its own with a concise message.

The plan will be executed by **1–3 separate Claude Sonnet 5.5 (Medium effort) sessions**, each starting with no memory of this one. Optimize the plan for that:

- Each session must be fully self-contained: it reads `planning/PLAN.md` and its own section, and nothing else is assumed.
- Make every design decision here. Sonnet should never have to choose between alternatives. Resolve ambiguities yourself, and list the assumptions you made in a short "Decisions" section.
- Be concrete: exact file/module tree, public function and class signatures, dataclass fields, CLI commands and flags, JSON schemas, config precedence rules, error-handling rules, and the test list per module. Do not write full implementations, but do give the interfaces and any tricky logic precisely.
- Split the work into the fewest sessions that keep each one comfortably in scope (target 2, at most 3). For each session give: goal, branch name, ordered task list, atomic-commit boundaries with suggested messages, and a "done when" checklist with the exact commands to verify (tests, coverage, lint). Put dependencies and shared interfaces first so later sessions can rely on them.
- Include a ready-to-paste kickoff prompt for each Sonnet session at the end of the plan.

## Before planning: investigate

Read the current code and the references. Do not guess.

- `jev_gmail_labeler/` (`gmail_api_util.py`, `main.py`, `parameters.py`, `scrub.py`), `pyproject.toml`, `README.md`, `ABOUT_JEV.md`, `.gitignore`
- `prompts/` (the two reference conversations below; ignore this prompt file itself)
- `sandbox/gmail-listener/` (listener, auth, state, watch renewal, systemd units)
- `workspace/` — **contains secrets and personal data. Look at file names and structure only. Never print, copy, or quote credentials, tokens, or email content into the plan or anywhere else.** Keep `workspace` in `.gitignore`. Also confirm `token.json` and `credentials.json` stay ignored.
- Use the `typesafe:typesafe-ai` skill and current TypeSafe SDK docs to find the correct, current way to call Jev, so the plan's TypeSafe subpackage matches the real API.
- Check current Google docs as needed for Gmail `users.watch`, Pub/Sub pull subscriptions, `history.list`, labels, and OAuth for long-running services. Confirm the details rather than relying on memory.

Note: git may report "dubious ownership" for this repo. If it blocks you, tell me the `safe.directory` command to run rather than changing global config yourself.

## Requirements (verbatim from me)

### Project

* Do all work in the current project (`W:\dev\sandbox\jev-gmail-labeler`)
* Preserve the active `workspace` and keep it in `.gitignore`. It contains secrets and personal data
* Make atomic commits with helpful but concise commit messages
* Complete larger units of work in their own branches, then when complete, merge that branch into main and push the changes to the remote

### Structure

* Reorganize all modules into a conventional `src/` folder hierarchy
  * All modules related to Google APIs go in their own subpackage
  * All modules related to the TypeSafe API go in their own subpackage
* After the rewrite, remove the `sandbox` directory and move `ABOUT_JEV.md` into `docs`.
* Keep two separate documentation directories:
  * `docs/` holds only documentation about using and setting up the application (user guide, `ABOUT_JEV.md`, and similar). The README links into it.
  * `planning/` holds documentation about how the project was planned and built: `PLAN.md` and the `prompts` directory (move `prompts` to `planning/prompts` after the rewrite). Nothing in `planning/` is linked from the README as user documentation.

### UI

* Keep `argparse` as the primary UI
  * Adjust the parameters, CLI arguments, and commands as needed to support this redesign
  * Add support for users to provide configuration via JSON string or file instead of the CLI arguments
* Email criteria are now provided by the user as a JSON file, instead of a hard-coded dictionary

### Testing

* The package must have at least 95% test coverage
* In the test suite, always mock every function call that would read from or write to a disk or network
* Prefer defining test data and mocks as `pytest` fixtures

### Current state

Today the user manually runs each step from the command line:

1. Fetch email messages via the Gmail API (`get-emails`)
2. Anonymize the bodies of those messages (`anonymize-emails`)
3. Classify those messages using Jev (`classify-emails`)
4. Generate a detailed report of Jev's classifications (`report`)

The labelling step is not implemented yet.

### Target state

`jev-gmail-labeler` is currently a proof of concept to evaluate how well Jev classifies my email. It handles the task well, so I want to rewrite it into a robust tool that I and others can use to keep email organized in real time using Jev and the Gmail API.

The application has two basic functions:

1. Listen for incoming emails (pull subscription on a Pub/Sub topic)
2. When an email arrives, run the labelling pipeline on it

The labelling pipeline is logically the same as today, plus one new step:

1. Receive email message
2. Anonymize the body of the message
3. Classify the message with Jev
4. Apply the appropriate label to the message, based on Jev's classification

These steps no longer read input from, or write output to, JSON files. They accept and return JSON, dicts, or dataclass instances instead.

### Expected usage

The application will primarily run as a `systemd` service. New emails get labelled as they arrive with no user interaction. It must also be able to run the labelling pipeline manually from the command line.

When run manually, the user can pick the most recent N emails, or the emails returned by a Gmail query. The tool runs the pipeline on each email and returns the classification results and chosen label as a JSON string, or writes them to a CSV or JSON file. The user can also choose whether to apply the label to the emails or just return the results.

### Documentation

Once everything is built, write a comprehensive user guide covering how to:

* Set up all the APIs, services, topics, credentials, etc. in GCP
* Use the tool as a `systemd` service
* Use the tool manually from the command line
* Maintain your email criteria in a JSON file

All documentation must be clear, concise, and in plain language. Use technical jargon only when necessary, and then judiciously, plainly, and concisely.

The README is the documentation entry point and includes instructions to install and use the application. The user guide is linked from the README.

### Reference

The two conversations in `prompts/` were used to create `gmail_api_util.py` and the example listener in `sandbox/gmail-listener`:

* `2026-10-03_gmail-api-util-session.md`
* `gmail-listener-conversation.md`

Do not plan to copy/paste code from `gmail_api_util.py` or `sandbox/gmail-listener` except where it truly makes sense. Use them only as context and examples. The rewrite is from scratch, designed for the app's new goals.

## What the plan must cover

1. **Architecture overview**: the target `src/` tree (with package name, the Google subpackage, the TypeSafe subpackage, pipeline, config, CLI, service/listener, models), `docs/` and `planning/` trees, `tests/` tree mirroring `src/`, and how modules depend on each other. Include what happens to each existing file (rewrite, delete, move).
2. **Data model**: dataclasses for email message, anonymized message, classification result, label decision, and pipeline result, with JSON (de)serialization.
3. **Email criteria JSON**: schema, a documented example, validation rules and error messages, how criteria map to Jev classification and to Gmail labels (including how missing labels are created), and what happens when no criterion matches.
4. **Configuration**: the JSON-string / JSON-file / CLI-argument / environment-variable precedence, the full config schema, and secrets handling (paths to credentials/token, TypeSafe API key). Nothing secret is ever logged.
5. **CLI design**: the full `argparse` command tree and flags (e.g. a `run`/`listen` command for the service, a `label` command for manual runs with `--count` / `--query`, `--apply`/dry-run, `--output` JSON/CSV, and `--config`), exit codes, and output formats. Decide whether a one-time auth / watch-setup command is needed.
6. **Listener/service**: Pub/Sub pull flow, Gmail `watch` creation and renewal, `historyId` tracking and where state is stored, handling of duplicate or out-of-order notifications, ack/nack semantics, retry/backoff, graceful shutdown, logging, and idempotency (never double-label). Provide the `systemd` unit file(s) as part of the plan.
7. **Reliability**: OAuth token refresh in a long-running process, rate limits, partial pipeline failure for one email without stopping the service, and large or HTML-only email bodies.
8. **Testing strategy**: shared fixtures (`conftest.py`) for fake Gmail responses, Pub/Sub messages, Jev responses, and criteria; the exact mocking approach so no test touches disk or network (including a guard such as an autouse fixture that fails on real network or file I/O); per-module test lists; how to reach and verify ≥95% coverage (`pytest-cov` config in `pyproject.toml` with `fail_under = 95`). Also say how the spaCy/Presidio model load is mocked or kept fast in tests.
9. **`pyproject.toml` and tooling**: `src` layout and build config, entry point, dependencies to add or drop, dev dependencies, ruff config.
10. **Documentation plan**: file list for `docs/` (usage and setup only), a section outline for the user guide and README, and which session writes each. Docs must be written last, after the code is verified.
11. **Git workflow**: branch per session, atomic commit plan, and merge to main and push at the end of each session (state clearly whether to merge only after tests and coverage pass).
12. **Risks and open questions**: anything I must do or decide myself (for example GCP console steps, a Pub/Sub topic IAM grant for `gmail-api-push@system.gserviceaccount.com`, or OAuth consent-screen "testing" mode token expiry). Flag these clearly, and keep it short.

## Quality bar

- The plan should be as short as it can be while still leaving Sonnet no design decisions. Prefer tables, trees, and signatures over prose.
- Every requirement above must map to a concrete task in a specific session. End the plan with a short requirements-to-task checklist so nothing is dropped.
- Before finishing, re-read your plan as if you were a fresh Sonnet session with only that file. Fix any gap, contradiction, or unstated assumption.

When done, reply with a brief summary (session breakdown and the open questions for me), and the path to the plan.
