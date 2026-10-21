"""Cloudzy Earth Intelligence: real disaster and hazard data, normalised into one event schema.

Modules
- schema: the shared `HazardEvent` record, categories, record kinds, severity ranks and freshness labels.
- sources: every connected provider with its coverage, update cadence, licence and attribution.
- http: one HTTP helper (timeouts, retries with backoff, HTTP 429 Retry-After) for JSON, text and bytes.
- usgs, gdacs, nhc, nws, firms, usdm, volcanoes, tsunami: one parser and fetcher per provider.
- store: per-source cache with last-good fallback, request de-duplication, cross-source duplicate merging
  and the static files the globe reads.
- analysis: filters, summaries and nearby-settlement context (geodesic distances, point-in-polygon).

Nothing here invents an event: every record comes from a provider response, and a field the provider
doesn't supply stays missing.
"""
