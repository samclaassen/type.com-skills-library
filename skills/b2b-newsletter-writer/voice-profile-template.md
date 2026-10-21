# Voice profile template

Fill this from `voice-stats` output plus a close read of the last 3 issues (or the
`voice_file`). Keep it under 200 words. Each line should be a rule the next draft can follow
and the reviewer can check. "Friendly and professional" is not a rule.
Show it to the user on the first run and ask for corrections.

```markdown
#### Voice profile — <newsletter name>
Source: <voice_file | issues: file1, file2, file3 | default (no past issues)>

- **Sentence length:** average <n> words (median <n>); <x>% of sentences are 8 words or fewer.
  Pattern: <e.g. a short claim, then one longer sentence that explains it>.
- **Person:** reader is "<you>"; company is "<we>"; author uses "<I>" only in <where>.
- **Formality:** <contractions per 1,000 words> contractions per 1k words; exclamation marks <never/rare>;
  jargon the audience uses: <SOW, utilization, ...>; jargon to avoid: <...>.
- **Opener:** <n> sentences; <how it works, e.g. contrarian claim + consequence>; never a greeting.
- **Headlines:** <sentence case / title case>, about <n> words, phrased as <a claim / an instruction>.
- **Recurring structures:** <e.g. every section ends with "**Steal this:** <one action>">.
- **CTA pattern:** <e.g. one direct question + "Hit reply.">
- **Sign-off:** "<exact sign-off>"
- **Banned for this voice:** <phrases the brand never uses, beyond rules.json>.
  Add these to `extra_banned_phrases`.
- **Sample line in voice:** "<one sentence lifted from a past issue that best shows the voice>"
```

## What to extract, and where to find it

| Signal | How to measure | Where it shows |
| --- | --- | --- |
| Sentence length | `voice-stats`: average, median, short/long share | Rhythm. Match the share of short punches, not just the average. |
| Person | `voice-stats`: I/we/you per 1,000 words | Who talks to whom. A high "you" rate means advice-led copy. |
| Formality | contractions and exclamation rates; read for slang and jargon | Contractions above about 30 per 1k reads conversational. |
| Opener | `voice-stats` opener sentences; read the first lines | The move the opener makes: a claim, a story, a number. |
| Headlines | heading count, words and case | Whether headlines are labels ("Podcast") or claims. Claims are better. |
| Recurring structures | bold labels found in 2+ issues; read for repeated section shapes | Keep them. Readers notice when a format changes. |
| Sign-off | last line, consistent or not | Copy it exactly. |
| Banned phrases | `voice-stats` lists any already used | If past issues use slop, don't copy it. Flag it to the user. |

## Default voice (no voice file, no past issues)

Plain, direct, second person ("you"), "we" for the company, contractions allowed, an average of
12–16 words per sentence with a few short punches, no exclamation marks. Sentence-case headlines
phrased as claims. Sign off with `from_name`. State in the review post that the voice is the
default and will improve once past issues are available.
