Read CLAUDE.md, docs/PLAN.md and docs/ROADMAP.md. We are starting Phase $ARGUMENTS.

1. Summarize the phase goal and its gate in 3 lines.
2. Check prerequisites from earlier phases actually work (run the tests, make up if relevant).
3. Propose the phase as small slices, one commit each, on branch phase$ARGUMENTS/<short-name>. Flag which slices have a security angle.
4. Wait for my approval, then work one slice at a time: implement, run make lint and make test, show the diff summary, and give me one "why" worth understanding. Pause before the next slice.
5. Leave ADRs as templates for me to write. At the end, check the phase gate and list what I must do by hand.