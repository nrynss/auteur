# Roadmap — where FoleyFlow goes from here

FoleyFlow generates and places sound effects against silent video: it analyses
the footage, proposes a timeline of foley events, generates each sound through
a commercial provider, and mixes them into a deliverable — with every
generated second traceable to the call that produced it and the money it cost.

This document is about what comes after the demo. There are two distinct
products it could become, and they are not stages of the same thing.

---

## The model: bring your own keys

Users arrive with their own Backblaze B2 credentials and their own AI provider
keys. FoleyFlow stores nothing on their behalf, resells no provider, and takes
no cut of anyone's inference spend.

This is a deliberate choice, not a limitation:

- **Their content stays theirs.** Masters, mixes and manifests live in the
  customer's own bucket, under their own credentials. Nothing is copied
  anywhere we control.
- **Their costs are their own.** The per-call cost ledger reports what *their*
  keys spent, against provider invoices they can independently verify. There is
  no markup to explain and no metering to trust.
- **Their limits are their own.** Provider rate limits belong to the account
  that owns them, so no customer can be throttled by another customer's usage.
- **Nothing is shared between customers.** Generated audio is cached into the
  bucket that paid for it. One customer's work never subsidises — or leaks
  to — another's.

Everything below follows from this decision.

---

## Two options

|  | **Self-hosted / white-label** | **Multi-tenant SaaS** |
|---|---|---|
| Who runs it | The customer, or a partner under their own brand | We do |
| User accounts | Not needed — their existing perimeter is the boundary | Required, built from the ground up |
| Central infrastructure | None | Required |
| Distance from today | Short | Substantial |
| Ongoing cost | Support per deployment | One service, many customers |

The short version: **self-hosting is close to ready; SaaS requires two
foundational systems that do not exist yet.**

---

## Option A — self-hosted and white-label

A customer or partner deploys FoleyFlow inside their own environment, under
their own brand, pointed at their own storage and their own provider accounts.

**Why this is close.** The application already ships as a single container with
the interface built in. Every credential is supplied by configuration. All
customer data lives in the customer's bucket. And FoleyFlow contains no
analytics, no usage reporting and no callback of any kind — a deployment
depends on nothing we operate, and keeps running whether or not we do.

It also sidesteps the largest gap in the product: a customer deploying behind
their own single sign-on, VPN or gateway supplies the access control that
FoleyFlow does not yet provide itself.

**What it takes:**

- **Configurable branding.** The product name, page titles and icon become
  deployment settings. The visual system is already built on design tokens
  rather than hard-coded colours, so re-skinning is a configuration file rather
  than a rewrite.
- **A documented deployment.** The GPU worker that generates audio runs as a
  separate service so the main application stays free of heavy ML
  dependencies — an intentional split that keeps the deployment small and
  cheap, and that a self-hosting partner needs stated plainly.
- **A licensing model**, which decides whether this is a product or a
  demonstration.

**What it costs.** Every deployment is a separate thing to version and support.
And a hosted service where each partner sees their own brand on their own
domain is not this option — that is Option B with additional routing.

---

## Option B — multi-tenant SaaS

We host FoleyFlow; customers sign in and connect their own storage and provider
keys.

Two foundational systems gate everything else.

**User accounts and access control.** FoleyFlow today has no concept of a user.
It was built as a single-operator tool, and the demonstration deployment is
open by design. Multi-tenancy needs identity, sessions, organisations and
membership, plus enforcement that a request can only ever reach the data its
owner is entitled to. This is new construction, not hardening, and it is the
prerequisite for everything else on this road.

**A place to keep accounts.** Today the object store *is* the database — every
project, timeline, run and cost record is an object in the bucket. Bring your
own keys breaks that cleanly: a customer's credentials cannot be stored in the
bucket those credentials open. So accounts, organisations, and encrypted
customer credentials need somewhere that is not the customer's storage.

The first version of that can stay simple — a store we control, holding only
account records and encrypted credentials, with the encryption keys held
separately in a managed secret service. A conventional database becomes worth
its operational cost later, and only for the account layer: customer artifacts
never move, so that transition stays a migration of account records rather than
of anyone's media.

**Then the rest follows:** resolving each request against the right customer's
storage, isolating cost and quota records per customer, safe credential
rotation that cannot corrupt a job already running, and moving generation out
of the web process into a worker pool.

---

## The design is not foley-specific

Foley is the first application of this system, not the shape of it. Three
layers are deliberately generic, and none of them mentions sound effects:

- **Providers are declarative.** Vendors are registered in a configuration
  file — adapter class, credential source, calling convention — and each
  generated item is routed to an endpoint at request time. Adding a model, a
  vendor, or a whole modality is configuration plus an adapter, not a change to
  the pipeline. Video analysis is already interchangeable between vendors this
  way.
- **The timeline is a document, not a data structure about audio.** It is a
  versioned, immutable list of things to generate and where they belong in
  time. Every edit commits a new version beside the old one, and the same
  document always renders the same output. Nothing in that contract is specific
  to what the generated asset *is*.
- **The cost and provenance ledger is modality-agnostic.** It records calls,
  prices and outcomes — one immutable entry per billed generation, whatever was
  generated.

What that reaches without architectural change: **dialogue and ADR, voiceover,
and music and ambience beds** — all of them timed assets generated against
video and mixed to a deliverable. **Audio description for accessibility** is
the same problem with a different prompt. Further out, the pattern holds for
any workflow where a model proposes timed media and a human approves it before
money is spent: generated b-roll, VFX passes, subtitle and caption tracks.

### Localisation is the clearest case

Dubbing a title into a dozen territories is not principally a translation
problem — it is a versioning and provenance problem, which is exactly what this
system was built around. The timeline is the invariant: every line keeps its
position, its duration and its scene, and only the generated audio changes. So
each language becomes a full immutable version of the same document, sitting
beside the others rather than overwriting them.

That answers the questions localisation teams actually struggle with. **Which
take shipped in which territory** — because every version is addressable and
every asset traces to the call, the model and the vendor that produced it.
**What did each language cost** — because the ledger records spend per
generated line, so a per-territory budget is a query rather than a
reconstruction. And **can we reproduce the version we shipped three years ago**
— because the same document deterministically renders the same output, and the
manifest records what it was made from.

Compliance and rights work in this industry runs on being able to prove those
three things. Most generative tooling makes them harder; this design makes them
a property of the artifact.

The wider claim is about the discipline rather than the medium. **An auditable,
reproducible, multi-vendor generation pipeline — where every asset is traceable
to the call that made it and the cost it incurred, and where nothing is
generated without a human approving the spend — is a general answer to
producing AI media at professional standards.** Foley is a demanding first
proof of it: it needs frame-accurate placement, deterministic re-rendering, and
honest money, all at once.

---

## Built on Backblaze B2

FoleyFlow is not merely stored on B2 — B2 is its system of record. Every
artifact, every timeline version, every provenance manifest and every cost
entry is an immutable object in the bucket. The audit trail *is* the object
store.

That makes the platform fit unusually well:

- **The pricing matches the workload.** B2 is $6.95/TB per month with egress
  free up to three times average monthly storage. Sound design is
  write-once-read-often — a director scrubs the same mix repeatedly through
  review — which is precisely the pattern a per-gigabyte egress charge
  penalises.
- **S3 compatibility is why bring-your-own-storage is cheap to offer.** Pointing
  FoleyFlow at a customer's own bucket is a credential change, not an
  engineering project.
- **It fits between existing tools rather than competing with them.** B2's
  media-workflow ecosystem — LucidLink, MASV, EditShare, Telestream, Quantum
  and others — moves, stores and manages footage. None of them generate sound.
  Footage arrives through those tools, FoleyFlow scores it, and the finished
  mix returns to the same bucket as an immutable, signed record.

The positioning is deliberately narrow: **the sound layer of a B2-based
post-production workflow, with per-call cost provenance nothing else in that
stack provides.**

---

## What is honest about the current state

FoleyFlow is a working single-operator application with an unusually complete
accounting layer: an immutable record of every billed call, per-project
budgets, provider quota tracking, and a rule that when cost recording fails the
interface withholds the numbers rather than showing figures it cannot stand
behind. That discipline was built for the demonstration's own credibility, and
it is the part of the product that transfers unchanged to either option above.

What it does not yet have is a front door. There are no user accounts, and the
demonstration deployment is deliberately open. Option A works around that by
placing the application inside a boundary the customer already operates; Option
B has to build one. That is the real distance between today and a hosted
product, and it is stated here rather than discovered later.
