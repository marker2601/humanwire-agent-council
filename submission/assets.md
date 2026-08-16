# HumanWire submission asset manifest

Status meanings: **available** = local safe asset can be selected; **recapture** = recreate after final QA; **entrant-provided** = the entrant must supply/retain it; **external** = depends on an organizer, hosting platform, or provider and is not locally verified.

| Asset | Status | Required evidence / handling |
| --- | --- | --- |
| Desktop screenshot: interactive Decision Room | recapture | Capture a safe selected saved event and the live graph after desktop QA; show no routes, addresses, tokens, or private answers. |
| Desktop screenshot: Reach and exact selected saved event | recapture | Show the synchronized conversation plus safe From/To/Generated values. |
| Desktop screenshot: Data | recapture | Show the synchronized saved result and final JSON/CSV controls; do not include private source data. |
| Desktop screenshot: meeting-ready state | recapture | Show the verified-overlap outcome and final downloads only; never show attendee addresses or availability windows. |
| Mobile screenshot: Decision Room at 390 px | recapture | Verify readable, unclipped controls and the visible **Standard agents · no external messages** boundary. |
| Mobile screenshot: Reach/Data at 390 px | recapture | Verify replay/export controls and safe content remain visible. |
| Master 105-second demo video | available | Final independently decodable 1920×1080 H.264/AAC master; shows the working Standard-agent product and explicitly labels unrecorded provider proof. |
| Public repository URL | available | `https://github.com/marker2601/humanwire`; signed-out access verified before submission. |
| Public product URL | available | `https://secondsignal.vercel.app/`; signed-out interactive run, visible Standard-agent boundary, stream completion, replay, and downloads verified. |
| Public video URL | available | `https://youtu.be/FxzhLqoscSE`; public oEmbed title/author/thumbnail verification passed. |
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

Never publish credentials, `.env` values, direct contact destinations, routes, conversation or message IDs, provider bodies, private answers, database files, or `.superpowers/brainstorm/` material. The public product and any recording must truthfully show **Standard agents · no external messages**; legacy frozen/synthetic proof remains separately labeled.
