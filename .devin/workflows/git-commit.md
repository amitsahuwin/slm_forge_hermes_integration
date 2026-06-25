---
description: Repeatable git workflow: diff, document commit, stage, and push
---
1. Inspect the current changes with `git status -sb` and `git diff` so you know what will be included.
2. Create a Markdown commit message file (e.g., `commit_message.md`) that contains Summary and Testing sections describing the work.
3. Stage everything with `git add -A` so the commit captures all tracked and new files.
4. Run `git commit -F commit_message.md` to use the Markdown file as the commit message.
5. Push to the remote with `git push` (adjust the remote/branch if needed).
