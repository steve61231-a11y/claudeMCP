# Pulse Intelligence — Data Handling Policy (working draft)

> Status: internal working draft for counsel review. Not legal advice.
> Jurisdictional reference: Kenya Data Protection Act, 2019 (ODPC).

## What we collect
- Publicly posted content (posts, videos, comments, news articles) that
  mentions tracked **public figures acting in their public capacity**.
- Public account metadata for authors of that content: handle, display name,
  follower count, platform. No private-account content, no DMs, no scraped
  personal contact details.

## What we do NOT do
- No profiling of private citizens. Authors of comments are analyzed only as
  public voices (handle + public post); we do not build dossiers on
  non-public individuals.
- No opposition research on private persons, family members not in public
  life, or minors — regardless of who asks.
- No inference of sensitive attributes (ethnicity, health, religion) about
  any individual.
- No content from closed/private groups or channels.

## Lawful basis (position to be confirmed by counsel)
Processing of publicly available statements about public figures for media
monitoring and political analysis, under legitimate interest, with the public
interest in political accountability weighed in. Public figures' public
statements attract a reduced expectation of privacy; we still apply data
minimisation (only what the analysis needs).

## Retention
- Raw mentions: retained 24 months, then deleted or anonymised (aggregate
  statistics may be retained indefinitely).
- Alerts and reports: retained for the life of the client relationship + 12
  months.
- Tipline submissions: same as raw mentions; submitter identity is never
  required or stored beyond an optional free-text hint.

## Access & security
- Dashboards are authenticated per client; a client sees only politicians in
  their own workspace. The only public surface is the aggregated Baraza
  Index (no personal data beyond public handles and public quotes).
- API access is key-gated and rate-limited; keys are stored as deployment
  secrets, never in the repository.

## Subject requests
Requests from data subjects (including tracked public figures) are logged
and answered within statutory timelines; verified requests for erasure of
non-public-interest personal data are honoured.

## Open items for counsel
1. Confirm legitimate-interest position and any ODPC registration duty.
2. Retention periods sanity-check against DPA 2019.
3. Terms for client misuse (e.g. attempting to target private citizens).
