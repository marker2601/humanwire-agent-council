# HumanWire DecisionOS fidelity ledger

Date: 2026-08-18

## Visual sources

- `decisionos-signin-concept.png`: signed-out and organization-onboarding specification.
- `decisionos-workspace-concept.png`: authenticated workspace specification.

The implementation uses code-native HTML, CSS, SVG icons, form controls, and
JavaScript. The concept images are design references only and are never served as
application UI.

## Comparison ledger

| Area | Concept evidence | Browser evidence | Resolution |
| --- | --- | --- | --- |
| Palette | True white, deep navy, precise cyan, cool gray rules | Matching computed tokens and rendered surfaces | Matched without gradients, glow, or glass effects |
| Signed-out hierarchy | Editorial headline left; one focused sign-in action right | 1680×950 preserves the same split and spacing rhythm | Matched; missing-config copy appears only in the isolated QA fixture |
| Decision method | Five connected stages with a visible human-approval boundary | Five stages and dashed boundary remain readable at desktop and mobile | Mobile rail compacted so the Google action remains in the first 390×844 viewport |
| Workspace shell | Quiet header, navy rail, open center canvas, authority inspector | 1680×950 renders the same three-part hierarchy | Sample people/emails were intentionally removed; the real organization role is shown instead |
| Readiness council | Five functional specialists connected in sequence | Five code-native stages, current state, status dots, and human boundary | Matched without avatars pretending to be real people |
| Evidence | Three restrained evidence rows | Market validation, financial runway, and customer proof rows | Empty copy is truthful until evidence intake is bound to an active run |
| Authority | Explicit owner/approver/viewer boundary and human approval state | Real organization role plus approval and viewer definitions | Matched; inert approval button replaced with explanatory status copy |
| Controls | Clear 44px actions with visible selection/focus | Browser audit reports zero visible controls below 44×44 | Matched at 1680×950 and 390×844 |
| Responsive behavior | Right pane and navigation collapse without losing the workflow | 390×844 has no page-level horizontal overflow and 14px minimum visible text | Matched; navigation becomes an icon rail with accessible labels |

## Above-the-fold copy check

The implementation preserves the locked brand, headline, support copy, sign-in
actions, workflow stages, workspace heading, navigation, primary actions,
playbooks, council, evidence, and authority labels. Added copy is limited to
functional sign-out, truthful empty states, secure invitation guidance, and
organization-scoped authority language. No fake metrics, guaranteed outcomes,
funding claims, or simulated-human claims were added.

## Interaction evidence

- Google identity exchange is sent once and cleared from controller memory.
- Email-link control reveals the real email form.
- Organization and workspace selectors load only authorized API projections.
- New decision opens the real workspace-creation flow.
- Invite teammate opens the real team/invitation panel.
- Navigation changes the selected real panel.
- Sign-out calls the protected session endpoint and the identity provider.
- Browser verification found no console warnings/errors, no mobile overflow, and
  no visible target below 44×44.
