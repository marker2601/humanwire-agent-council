# Caspian Buildathon submission checklist

Verified against the official Devpost rules and the deadline-extension update on 10 August 2026.

## Eligibility and timing

- [ ] Entrant is at least 18 years old under the detailed official rules.
- [ ] Team contains one or two people, and each person belongs to only this team.
- [ ] Entrant is not excluded by organizer, judge, family, sanctions, or local-law restrictions.
- [ ] All submitted project code was written during the hackathon window.
- [ ] Submit by the extended deadline: **16 August 2026, 11:59 PM IST**. Devpost may display this as 17 August at 12:00 AM IST.

## Qualifying implementation

- [x] Agent uses `caspian-sdk` (`caspian-sdk==0.6.1`).
- [x] Agent runs on at least two supported channels: email and Telegram.
- [x] Both channels enter exactly one `on_message` handler.
- [x] Email and Telegram share one workflow rather than duplicated handlers.
- [x] Setup instructions are included in English.
- [x] Project is original work and uses permitted open-source libraries, models, and AI coding assistance.

## Public repository

- [ ] Push the final `feat/secondsignal` history to the public GitHub repository.
- [ ] Confirm the repository is publicly accessible in a signed-out browser.
- [ ] Keep the repository public through judging.
- [ ] Confirm `.env`, API keys, `data/identities.json`, databases, captured addresses, and conversation IDs are absent from Git history.
- [ ] Confirm README installation commands work from a fresh clone.
- [ ] Add the final public repository URL to Devpost.

## Required demo video

- [ ] Record a real working demo, not a mocked, staged, or edited-to-look-working flow.
- [ ] Show the agent running on both email and Telegram.
- [ ] Show the same live case token in the origin acknowledgement, independent-channel challenge, human response, and final receipt.
- [ ] Show the independent human reply coming from the registered channel.
- [ ] Show the final verdict returning to the original conversation.
- [ ] Briefly show evidence that both channels use one handler.
- [ ] Keep the video at three minutes or less; target the clear 60-second script.
- [ ] Host the video publicly on YouTube, Vimeo, or Loom.
- [ ] Test the video URL in a signed-out browser with audio enabled.
- [ ] Add the final video URL to Devpost.

## Non-negotiable live checks

- [ ] `GET /health/live` returns HTTP 200.
- [ ] `GET /health/ready` returns HTTP 200 immediately before recording.
- [ ] Email channel status is `ready`.
- [ ] Telegram channel status is `ready`.
- [ ] Listener heartbeat is fresh.
- [ ] Telegram reporter address matches the local registry.
- [ ] Email verifier address matches the local registry.
- [ ] Telegram verifier conversation was created by a real inbound message.
- [ ] Gift-card denial completes Telegram → email → Telegram.
- [ ] Reverse route completes email → Telegram → origin conversation.
- [ ] Invalid sender response is rejected.
- [ ] Duplicate response cannot alter a terminal verdict.
- [ ] Timeout ends as `UNVERIFIED`.
- [ ] Dashboard contains no private reporter or verifier address.
- [ ] Dashboard exposes no POST, PUT, PATCH, or DELETE action.
- [ ] No credentials or private identifiers appear in terminal output or recording.

## Verification commands

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\smoke_check.py
.\.venv\Scripts\python.exe -m ruff check src tests scripts
git status --short
git log --oneline --decorate -15
```

- [ ] Test suite passes.
- [ ] All three smoke scenarios pass.
- [ ] Ruff passes.
- [ ] Worktree is clean.
- [ ] Final commit exists on the public branch.

## Devpost entry

- [ ] Project name is **SecondSignal**.
- [ ] Tagline is **Verify urgent requests through a channel the attacker does not control.**
- [ ] Devpost narrative is copied from `submission/devpost.md` and reviewed for accuracy.
- [ ] Public repository URL is attached.
- [ ] Public demo video URL is attached.
- [ ] Submission is in English.
- [ ] Final page preview contains no missing media or broken links.
- [ ] Submit once and save confirmation evidence before the deadline.

## Official references

- [Caspian Buildathon overview](https://caspian.devpost.com/)
- [Detailed competition rules](https://caspian.devpost.com/rules)
- [Deadline-extension update](https://caspian.devpost.com/updates)
