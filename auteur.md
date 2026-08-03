# Auteur

**Sound design for video, where being fussy is free.**

Auteur turns a silent or under-designed video into a finished, mixed soundtrack
that you can interrogate: every sound traceable to the call that made it, every
placement adjustable without paying to move it, every version kept beside the
last.

Live: <https://auteur.nryn.dev>

Built for the Backblaze Generative AI Media Hackathon. Foley is the first
application, not the boundary of the design.

---

## The argument

An auteur is the person with a specific idea of what the film should be, who
keeps pushing until the thing on screen matches the thing in their head. The
defining trait is refusal to accept the default.

That is exactly what generative tools erode. Not because the output is bad,
which it often isn't, but because iterating is slow, costly, or impossible, so
"fine" becomes "shipped".

**Slop is not a quality problem. It is an economics problem.** People accept a
first take when a second one costs too much.

Auteur's answer is its cost geometry. Moving an event is free. Resizing is
free. Renaming and deleting are free. Only *generating* costs money, and it
tells you the price before it spends. So you can be as particular as you like
about placement, which is the thing that actually decides whether foley reads
as real, without paying for an opinion.

We made rigour affordable. Everything else follows from that.

**The model proposes; a person decides.** Director mode cannot apply an edit.
It returns a diff, ghosting the old positions against the new, and a human
approves or rejects it. The AI has no commit rights.

**An approved take stays approved.** The same timeline in produces a
byte-identical mix out. Nothing drifts between sign-off and delivery.

**Being wrong is cheap.** Every commit is a new version beside the old one, so
exploring costs nothing and reverting is one click.

**Every second is accountable.** Each generated sound traces to the call that
made it and the money it cost.

There is a competitive version of this argument, if it is ever needed. Video
models are starting to emit audio directly. That gives you a track you cannot
interrogate, cannot nudge by 40 ms, cannot version, and cannot price. Fine for
slop. Disqualifying for craft.

---

## How it feels to use

You upload a video. Auteur watches it and proposes a timeline: a list of sounds
it thinks belong, each pinned to a moment and a scene.

You disagree with some of it. That is the point. You drag events, nudge them by
10 ms or 1 ms, stretch their windows, rename them, delete the ones that are
wrong. None of that costs anything, and none of it re-rolls the texture of a
sound you already like, because the seed is frozen the moment you touch an
event. A nudge translates a sound in time. It does not gamble with it.

When you are happy with the placement, you generate. Auteur states the cost
first and waits for you to confirm. Then it mixes, and you can A/B the result
against the original, or scrub a browser preview that schedules the stems over
your video for free while you decide.

If one footstep is weak, you regenerate that one stem. Not the production.

The mix is measured, not graded: loudness and true peak against their targets,
adjacent shots compared side by side. Auteur shows you the numbers and the
comparison. It does not tell you your film is good.

When you export, you get the mix and a full inventory of what produced it.

---

## What it does today

**Sound design.** Video analysis into a proposed timeline of events. Sound
generation per event through a tiered provider path. Deterministic mixing and
muxing back onto picture.

**Editing that costs nothing.** Drag, resize, arrow-nudge at three
granularities, rename, delete. Draft state held locally until you commit;
committing writes a new immutable version, and old versions stay one click
away.

**Director mode.** Describe what you want in chat. The model returns a proposed
diff, validated against the timeline schema and rendered as ghosted positions
against the current ones. You approve or you don't. Every turn is billed and
recorded.

**Money in the open.** Cost stated before any billable action, with explicit
confirmation. Per-project budgets with alerts. A transaction record on
durable storage, one immutable row per call, with the endpoint that actually
served it recorded alongside the one that was requested. If the spend record
cannot be written, Auteur withholds the figures rather than showing numbers it
cannot stand behind.

**Evidence, not verdicts.** Loudness and true peak reported against targets.
Shot-to-shot comparison shown as paired frames, never as a "fault". A measured
number never renders without naming the artifact it was measured from.

**A production record.** Source video, scenes, timelines, stems, mixes,
exports, quality measurements, the event log, runs, and the transaction record
all live in Backblaze B2. Completed generations are immutable. Project status
is derived by folding the event log rather than stored in parallel, so it
cannot disagree with what actually happened.

**Recovery.** Material that lost its project, and spend that lost its
attribution, are listed and can be claimed back rather than silently orphaned.

**An operator surface.** A read-only configuration screen reports whether
storage, the rendering worker, spend recording, and daily request limits are
actually wired up, and which build is deployed.

The interface is a control room, not a status page. It has a keyboard map, a
glossary, and accessibility and layout gates that run on every push.

---

## What it deliberately doesn't do

The product argues for honesty, so this section belongs here.

**There is no authentication.** Auteur has no users, no orgs, and no access
control. Anyone who can reach the deployment can use it. Today that is
acceptable because the intended perimeter is your own infrastructure. It is
the single largest gap between what exists and a product you could sell
seats to.

**It does not resell inference.** There is no margin on generation and no
account system to bill against.

**It is one instance.** Generation runs in the web process. That is fine for
a single operator and wrong for concurrent tenants.

**Automatic retry is narrow.** Provider or network failure retries once.
A take you simply dislike is an artistic judgment, and artistic judgments bill
only after you confirm them.

---

## Where it goes

The full argument is in [`docs/roadmap.md`](docs/roadmap.md). The shape of it:

### Bring your own keys

Customers supply their own storage and provider credentials. Auteur does not
resell inference, does not hold customer masters on its own account, and does
not take a cut. Content, cost, and rate limits stay with the customer. This is
a deliberate position, not a limitation waiting to be fixed: it removes the
two hardest problems in the category, which are liability for other people's
footage and margin on other people's inference.

### Self-hosted and white-label, near term

A single container, configured by secrets, that does not phone home. The brand
is already externalised at build and runtime, so a customer's deployment can
carry their name rather than ours. What it still needs is a documented deploy
covering the app and GPU worker split, and a licensing decision.

The access control gap closes here by borrowing the customer's perimeter: SSO,
VPN, whatever already guards their infrastructure.

### Multi-tenant SaaS, further out

This needs two things Auteur does not have. Identity, meaning real users, orgs,
and enforcement. And an account store that is not the customer's bucket,
because encrypted credentials cannot live inside the storage those credentials
open. After that: per-request storage resolution, isolated spend and quota
accounting, safe credential rotation, and generation moved out of the web
process.

### The design is not foley-specific

The pattern underneath is a timeline of events, a set of providers, a
deterministic assembly step, and a cost record. Nothing about that is specific
to footsteps. Without architectural change it reaches dialogue and ADR,
voiceover, music and ambience beds, and audio description. Further out, timed
b-roll, VFX passes, and captions.

The strongest non-foley case is **localisation and dubbing**: versioned
timelines per territory, each with its own cost and provenance trail. That is
the same product with a different vocabulary.

Foley is a demanding first proof precisely because it is unforgiving.
Frame-accurate placement, deterministic re-render, honest money. Anything that
survives foley survives the easier modalities.

---

## Under the hood, briefly

One Python package, one CLI, one Svelte SPA built into the same image, deployed
on Cloud Run.

**Genblaze** is the orchestration spine. Provider calls, mixing, muxing and
quality measurement all run as timed, recorded stages rather than as special
cases, so a mix is accounted for the same way a paid API call is.

**Backblaze B2** is the system of record, not just the export destination. Its
pricing and S3 compatibility are what make bring-your-own-bucket a credential
change rather than a rewrite.

**Providers are configuration.** A single `providers.toml` is the only place a
vendor is named. Adding one is a config entry plus an adapter, not a new code
path. Sound generation is tiered: a paid path for hero foley, a fallback for
when it fails, and a self-hosted worker used for free testing. The GPU worker
is a separate process reached over HTTP, so nothing in the main codebase
imports a model runtime.

For anything more specific than this, the technical documents are better than a
summary of them.

---

## Related documents

| Document | What it is for |
|---|---|
| [`docs/api.md`](docs/api.md) | The HTTP contract, and the source of truth for routes |
| [`docs/roadmap.md`](docs/roadmap.md) | Self-host versus SaaS, and the modality future |
| [`README.md`](README.md) | Running and deploying it |

---

*Auteur is the product name. Package paths, environment variables and some
deploy identifiers still read `foleyflow`, which is history rather than
branding: they are code and ops surfaces, not anything a user sees.*
