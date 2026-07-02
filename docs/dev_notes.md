# Dev Notes (Jules — personal)

## Standing Claude prompt (paste at the start of every session)
> You are a professional software engineer working on a live, regression-sensitive
> codebase. Your goal is steady, verifiable progress — not speed. Make the smallest
> correct change, explain the root cause before editing, and never modify code I
> didn't ask you to.

## Log-cleaning command (recovered 2026-07-02 from zsh history)
Copy the Render log block, then run this to strip noise and put the clean log back on
the clipboard (paste that into Claude):
```bash
pbpaste | grep -vE 'httpx|raw slot\(s\)|barge-in: partial|Redis (read|write) error' | pbcopy
```
Variant that also prints to the terminal while cleaning the clipboard:
```bash
pbpaste | grep -vE 'httpx|raw slot\(s\)|barge-in: partial|Redis (read|write) error' | tee >(pbcopy)
```
Strips: httpx access lines · `raw slot(s)` dumps · `barge-in: partial` spam ·
`Redis (read|write) error`. Removing that noise also keeps the paste under the length
limit (avoids truncation).
