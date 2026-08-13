# HumanWire analytics and Power BI import

HumanWire exposes one canonical, redacted outreach-event projection through its technical Data page, JSON API, and CSV download. These surfaces are read-only snapshots of persisted events; consumers own refresh scheduling, and there is no realtime guarantee.

## Endpoints and authentication

- JSON: `{base_url}/api/v1/mandates/{mandate_token}/outreach-events`
- CSV: `{base_url}/api/v1/mandates/{mandate_token}/outreach-events.csv`
- Browser table: `{base_url}/mandates/{mandate_token}/data`

In non-demo deployments, every `/api/v1/*` request requires this header:

```text
Authorization: Bearer <read-only-token>
```

Never put a token in a query string, shared URL, screenshot, source control, or Power BI source text checked into the repository. The public HTML Data page contains no API credential.

For authenticated JSON in Power BI Desktop, choose **Get Data**, select **Web**, enter the JSON endpoint, and configure the `Authorization` request header in the credential flow available to your managed Power BI environment. Expand the returned records using the stable field order below. A downloaded CSV is the simpler offline path when managed header authentication or scheduled API refresh is not available.

## Stable fields

Each HTML, JSON, and CSV row contains exactly these fields in this order. Unknown or unavailable optional values are empty strings.

1. `mandate_token` — public mandate token; text.
2. `timestamp` — persisted event time normalized to ISO-8601 UTC; datetime text.
3. `initiator_id` — redacted-safe mandate initiator identifier; text.
4. `source_department` — exact persisted initiator-assignment department when uniquely identifiable; text.
5. `target_person_id` — exact persisted target identifier for a uniquely bound assignment; text.
6. `target_department` — exact persisted target department for a uniquely bound assignment; text.
7. `direction` — lowercase persisted engagement direction; text enum.
8. `channel` — lowercase allowed channel saved on the event; text enum.
9. `engagement_type` — lowercase persisted adaptive engagement type; text enum.
10. `response_required` — whether the engagement requires a human response; JSON boolean and CSV/HTML `true` or `false`.
11. `engagement_status` — truthful current status from the HumanWire engagement projection; text enum.
12. `event_type` — allowlisted persisted event type; text.
13. `previous_state` — allowlisted persisted state before the event; text enum.
14. `new_state` — allowlisted persisted state after the event; text enum.
15. `outcome` — small deterministic allowlisted event outcome label; text.
16. `response_latency_seconds` — whole seconds from first contact to the first exact required human response; integer or empty string.

Response latency is assignment-level and repeats on that assignment's rows. `inform` is always blank because delivery is not a human response. Acknowledgement, interview, approval, and availability latency is emitted only when the exact persisted aggregate identity and completion proof match.

## Exact filters

The Data page, JSON, and CSV accept the same nine case-sensitive, exact-match query filters. Each filter may appear at most once. Invalid values return a safe `400` rather than broadening the result.

- `engagement_type` — one supported lowercase engagement type.
- `engagement_status` — one supported lowercase engagement status.
- `department` — an exact safe target department.
- `person_id` — an exact safe target person identifier.
- `channel` — one supported lowercase channel.
- `direction` — one supported lowercase direction.
- `event_type` — an exact safe event type.
- `timestamp_from` — inclusive offset-aware ISO-8601 lower bound, normalized to UTC.
- `timestamp_to` — inclusive offset-aware ISO-8601 upper bound, normalized to UTC.

Credential-free, URL-encoded examples:

```text
{base_url}/api/v1/mandates/{mandate_token}/outreach-events?engagement_type=structured_interview&engagement_status=in+progress
{base_url}/api/v1/mandates/{mandate_token}/outreach-events.csv?department=Support+Leadership&timestamp_from=2026-08-11T15%3A00%3A00%2B00%3A00&timestamp_to=2026-08-11T16%3A00%3A00%2B00%3A00
```

## Refresh and limitations

API and CSV responses are read-only snapshots of persisted events. Power BI or another consumer decides when to request a new snapshot; HumanWire does not promise push updates or realtime refresh.

The projection intentionally uses redacted identifiers and blank unknown or unavailable fields. It contains no arbitrary event metadata, raw private evidence, interview questions or answers, proposal or change text, objectives, reasons, provider bodies, routes, message identifiers, contact destinations, availability windows, credentials, or operational UUIDs. Response latency covers required human responses only; `inform` remains blank.

You must never connect Power BI or any BI tool directly to `humanwire.db`; do not copy or upload the operational SQLite file. HumanWire does not claim Power BI certification, organizer endorsement, production security certification, or live data beyond what these persisted snapshots demonstrate.
