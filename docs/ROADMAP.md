# Roadmap

Supremely is being built iteratively. This roadmap communicates direction
without pretending that early assumptions are permanent.

## The four components

Supremely is described to users as four things. Every release below is a
statement about how far each of them has come.

| Component | What it means |
|---|---|
| **CMS (publishing)** | Pages and posts an organization publishes on its own site, in its own theme, with drafts, visibility, and a public archive. |
| **Discussions (forum)** | Persistent, readable conversation: groups, topics, threaded replies, reactions, and the moderation tools to keep it healthy. |
| **Newsletters** | An audience the organization owns, and a way to send published content to it through the organization's own email provider. |
| **Membership** | Who belongs, what they can see, and eventually what they pay. |

## Now: v1.0

Establish the first coherent, installable version of Supremely: one that can be
self-hosted, used by real communities, and evaluated through actual use.

The scope is defined in [the v1.0 release plan](releases/v1.0.md) and tracked
in the GitHub v1.0.0 milestone. Against the four components, v1.0 delivers:

- **CMS**: pages and posts. Done, with editor and Videos fixes open from
  using the product in earnest. Structured content is open: rendered fields,
  richer field types, per-organization type configuration, site entry points,
  parent/child content and body directives, in six stages (see section 4 of
  the release plan). Types stay code-declared.
- **Discussions**: basic threading, replies, reactions, and moderation. Done.
- **Newsletters**: sending through Gmail SMTP works today. The Mailgun
  integration (#104), a documented Gmail recipe (#105) and a proper HTML
  layout for every email Supremely sends (#89) are open.
- **Membership**: tiers, with Free and Private built in and content gated by
  tier (#85). Open, and the largest remaining piece of feature work.

Success means we have something real enough to use, criticize, change, and
build upon.

## Next: v2.0

v2.0 is where the assumptions of v1.0 get tested by people who were not in the
room. The items already known to belong here:

- **Custom content**: an organization defines its own content type from the
  console, without code. In v1.0 a type is registered by core or by a plugin.
- **Import from other platforms**: WordPress, Ghost, Discourse, and others as
  demand appears (#79). Without this, an established community has to
  recreate everything by hand.
- **Paid tiers**: Stripe Checkout and webhooks on top of the v1.0 tier model
  (#86).
- **Hosted audio and video**: direct mp3 and video upload with streaming
  playback, rather than embeds from a URL (#84).
- **AI summaries**: a generated short blurb on post preview cards (#103).

Beyond those, let actual experience with v1.0 determine the priorities. Likely
work will include strengthening the core product, removing assumptions that
proved wrong, improving developer and user experience, and addressing the
first meaningful feedback.

## What this roadmap is not

It is not a contract.

We expect pivots. We will update this document as we learn.
