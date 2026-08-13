# HumanWire submission asset manifest

Status meanings: **available** = local safe asset can be selected; **recapture** = recreate after final QA; **entrant-provided** = the entrant must supply/retain it; **external** = depends on an organizer, hosting platform, or provider and is not locally verified.

| Asset | Status | Required evidence / handling |
| --- | --- | --- |
| Desktop screenshot: Decision Room mandate state and safe preview/release story | recapture | Capture only the synthetic, read-only fixture after desktop QA; show no routes, addresses, tokens, or private answers. |
| Desktop screenshot: Reach replay flow strip and exact selected persisted event | recapture | Show the synthetic provenance labels and safe From/To/Generated values. |
| Desktop screenshot: Data page with filtered redacted JSON/CSV controls | recapture | Preserve a safe filter; do not include bearer tokens or private source data. |
| Desktop screenshot: meeting-ready state and ICS download | recapture | Show verified-overlap outcome only; never show attendee addresses or availability windows. |
| Mobile screenshot: Decision Room at 390 px | recapture | Verify readable, unclipped controls and synthetic/read-only labeling. |
| Mobile screenshot: Reach/Data at 390 px | recapture | Verify replay/export controls and safe content remain visible. |
| Master 75–90 second demo video | recapture | Record the deterministic public fixture; narrate synthetic proof, six contracts, one handler/two channels, confirmation, two-round cap, meeting proof, safe exports, and limitations. |
| Public repository URL | entrant-provided | Insert only after the entrant publishes the intended repository and verifies signed-out access. |
| Public demo URL | entrant-provided | Current continuity target is `https://secondsignal.vercel.app`; re-verify signed-out, synthetic/read-only behavior before use. |
| Public video URL | entrant-provided | Insert only after upload and signed-out playback verification. |
| Caspian eligibility confirmation | external | Retain organizer rules/form evidence; do not infer eligibility from local docs. |
| ML Empowerment eligibility confirmation | external | Retain event-specific organizer evidence. |
| Build Beyond eligibility confirmation | external | Retain event-specific organizer evidence. |
| Caspian registration proof | entrant-provided | Preserve a privacy-safe confirmation/receipt from the registration flow. |
| ML Empowerment registration proof | entrant-provided | Preserve a privacy-safe confirmation/receipt from the registration flow. |
| Build Beyond registration proof | entrant-provided | Preserve a privacy-safe confirmation/receipt from the registration flow. |
| Caspian final submission receipt | entrant-provided | Save final confirmation, timestamp, and submitted links. |
| ML Empowerment final submission receipt | entrant-provided | Save final confirmation, timestamp, and submitted links. |
| Build Beyond final submission receipt | entrant-provided | Save final confirmation, timestamp, and submitted links. |
| Private live-provider proof | external | Requires a separate operator-owned Caspian/provider deployment, consenting test identities, and the documented three-flow checklist. Keep only safe timestamps/tokens/screenshots. |
| Offline fake-Caspian proof output | available | Use only as synthetic/offline proof; label it `transport=fake_caspian` and never present it as a live-provider receipt. |

## Do-not-capture rules

Never publish credentials, `.env` values, direct contact destinations, routes, conversation or message IDs, provider bodies, private answers, database files, or `.superpowers/brainstorm/` material. The public demo and any recording must remain synthetic and read-only.
