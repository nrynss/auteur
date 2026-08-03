# Archives

Development documents that are no longer maintained. They are kept because the
codebase cites them by section, not because they describe the product as it
stands today.

| Document | What it was | Why it is here |
|---|---|---|
| `SPEC.md` | The build specification | Superseded as a product description by `auteur.md`; still the referent for the `SPEC.md §N` citations throughout `src/foleyflow/` |
| `control-room.md` | The locked interface design direction | Its decisions shipped; still the referent for the `control-room.md §N` citations throughout `ui/src/` |
| `control-room-mockup.html` | Static mockup behind that direction | Superseded by the real interface |
| `handoff.md` | The engineering queue, tasks 1 to 23 | All 23 tasks are done. It is the record of what was built and why each decision went the way it did |
| `handoff-task7.md` | Task 7's own handoff, the control room build | Task 7 shipped and its remaining unit was promoted into task 22. Cited by `scripts/latency_live.py` §7.18 for the pre-fix latency numbers |

**These are historical.** Where they disagree with the code, the code is right.

## About the citations

Roughly thirty comments in `src/foleyflow/` cite `SPEC.md §N`, and roughly
thirty in `ui/src/` cite `control-room.md §N`. Those citations were left as
written rather than rewritten to chase this move: they name a section, which is
the part that matters, and rewriting fifty-odd files to update a path would
have buried a real change under churn. This file is how you find what they
point at.

## Current documents

| Document | What it is for |
|---|---|
| [`auteur.md`](../../../../auteur.md) | What the product is, its argument, and where it goes |
| [`docs/api.md`](../../../api.md) | The HTTP contract, and the source of truth for routes |
| [`docs/roadmap.md`](../../../roadmap.md) | Self-host versus SaaS, and the modality future |
| [`README.md`](../../../../README.md) | Running and deploying it |
