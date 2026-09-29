# Security policy

## Reporting a vulnerability

Please report security problems privately, using GitHub's **Report a
vulnerability** button on this repository's Security tab (private
vulnerability reporting). Do not open a public issue or pull request for them.

Include what you found, how to reproduce it, and the version (`python3 -m
annunciator version`). Remove your own addresses, host names and keys from
logs before sending. You should get an acknowledgement within a week.

## Supported versions

Fixes go into the latest release on the default branch.

## Scope

The [security model](docs/security.md) describes what Annunciator protects and
what it deliberately does not. In particular, read endpoints are
unauthenticated by design, so "anyone on my network can see my dashboard" is a
documented property rather than a vulnerability; a way to run a control without
the key, to read files outside `web/`, or to leak a key would be one.
