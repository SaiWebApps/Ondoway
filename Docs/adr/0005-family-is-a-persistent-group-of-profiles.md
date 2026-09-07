# A family is a persistent group of profiles

A family exists as its own node, joined by profiles: `Profile -[:MEMBER_OF]->
Family`. Membership hangs off the profile, not the user, because everything a
family coordinates already belongs to profiles — trips are captained and
crewed by profiles, lens preferences live on profiles, and a child is a
profile under a parent's user. A couple is a family of two.

One person invites the others with a shareable link that also renders as a
QR code, minted from the same token machinery as the magic-link sign-in; the
link travels over any channel, and the QR serves the family standing in the
same room. A trip's crew derives from family membership through the
`IS_CREW_OF` edge the schema already declares.

The group changes nothing the walker hears. It gives the planner the party's
composition and gives every member the same day; per-member narration is its
own later decision.

## Considered Options

- **Per-trip crew only** — no persistent group to land in, nothing for child
  accounts to hang from, and every trip re-asks who the family is.
- **Membership on the user** — splits family coordination from the profiles
  that hold every other coordinated thing.
- **Email-only invites** — forces an email address for someone in the same
  kitchen.
