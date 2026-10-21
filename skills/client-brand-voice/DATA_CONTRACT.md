# Data contract: client-brand-voice

## Files this skill reads and writes

```
clients/
  <client-slug>/
    client.json                  read; required; format in CLIENTS.md ("name" required)
    brand-voice.md               written by Mode 1 after approval; read by Modes 2 and 3
    brand-voice-YYYY-MM-DD.md    earlier version, archived on overwrite (never deleted)
```

- `<client-slug>` matches `^[a-z0-9]+(-[a-z0-9]+)*$`.
- `clients/` sits in the user's working folder. Scripts receive it through
  `--clients-dir` (default `./clients`).
- Every file is UTF-8 text.
- The skill never writes outside `clients/<slug>/` for the client named in the
  current run, and never writes anywhere without approval in the conversation.

## brand-voice.md

This is a Markdown file that follows `references/guideline-template.md`. The
prose is for people and the model. The script reads **only** the single
fenced block whose info string is `voice-rules`:

````markdown
```voice-rules
client: harbor-and-vine
updated: 2026-09-01
status: draft
ban: cheap
ban: limited time only
prefer: members | customers
prefer: bottles | products; SKUs
max_sentence_words: 20
exclamations: none
```
````

### Block rules

- The opening fence is a line that is exactly ```` ```voice-rules ````
  (trailing spaces allowed). The block closes at the next line that is
  exactly ```` ``` ````.
- The file must contain exactly one such block. Zero, two or more, or an
  unclosed block is malformed.
- Each line holds one `key: value`. Blank lines and lines starting with `#`
  are ignored. Keys are case-sensitive, and whitespace around keys and values
  is trimmed.

| Key | Required | Repeatable | Value | Meaning |
|---|---|---|---|---|
| `client` | yes | no | slug | Must equal the folder slug. If it doesn't, the script refuses to run, which stops one client's rules being applied to another's copy. |
| `updated` | yes | no | `YYYY-MM-DD` (a valid date) | When the guidelines were last changed. Mode 3 reports this date, not the file's modified time. |
| `status` | yes | no | `draft` or `approved` | Only the account lead sets `approved`. |
| `ban` | no | yes | phrase | Never use this word or phrase. |
| `prefer` | no | yes | `preferred \| avoided[; avoided…]` | Use X not Y. Each avoided term is reported with the preferred one. |
| `max_sentence_words` | no | no | positive integer | Sentences longer than this are reported. |
| `exclamations` | no | no | `none`, `allow` or `max N` | Exclamation mark policy for the whole draft. The default is `allow`. |

These count as malformed (exit 2): an unknown key, a missing `:`, an empty
value, a repeated single-use key, a missing required key, a bad slug, date,
status, integer, or exclamation value, a `prefer` without `|` or with an empty
term, a term that is both preferred and avoided, or a term that is both
preferred and banned.

### Matching semantics

- `ban` and `prefer` (avoided side): case-insensitive and whole-word, meaning
  the match can't touch a letter or digit on either side. Internal whitespace
  in a phrase matches any run of whitespace. Matches stay within one line.
  There's no stemming, so list plural forms separately.
- Sentences: blank lines, Markdown headings (`#`), and list items (`-`, `*`,
  `+`, `1.`, `1)`) end a sentence. Otherwise a sentence ends at `.`, `!` or
  `?` (plus any closing quotes or brackets) followed by whitespace or the end
  of the line. Sentences can run over several lines and are reported at the
  line where they start. Words are runs matching
  `[A-Za-z0-9][A-Za-z0-9'’-]*`.
- Exclamations: every `!` counts. Under `none`, each one is reported. Under
  `max N`, each one after the Nth in the draft is reported.

## Script: scripts/check_voice_rules.py

Python 3.9+ standard library only. No network access and no writes.

```
check_voice_rules.py --client SLUG --draft FILE [--clients-dir DIR]
check_voice_rules.py --list [--clients-dir DIR]
```

### Check output (stdout)

```
client: <slug> (<name from client.json>)
rules: <clients-dir>/<slug>/brand-voice.md (updated <date>, status <status>)
draft: <draft path>
  L<line>:<col> <rule>: <message>
result: <N> violation(s)[ (<rule> <count>, ...)]
```

- `<rule>` is one of `banned`, `prefer`, `sentence-length`, `exclamation`.
- Lines and columns are 1-based. Sentence-length hits use column 1 of the
  line where the sentence starts.
- Hits are sorted by line, then column, then rule, in the order `banned`,
  `prefer`, `sentence-length`, `exclamation`. The output depends only on the
  input.

### List output (stdout)

One line per sub-folder of `--clients-dir`, sorted by slug:

```
<slug> (<name>): updated <date>, status <status>, <n> banned, <n> prefer
<slug> (<name>): no brand-voice.md
<slug>: INVALID - <reason>
clients: <total>, invalid: <n>
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Check found no violations, or the list had no invalid clients |
| 1 | Check found one or more violations |
| 2 | Malformed input or usage error: a missing or non-UTF-8 file, a bad `client.json`, a malformed or missing rules block, a client mismatch, bad arguments, or (for `--list`) any invalid client. The message goes to stderr as `error: ...`, except invalid `--list` rows, which are printed inline. |
