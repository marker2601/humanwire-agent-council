# Project Scope

## Project Name Candidates

- HumanWire
- HumanWire Decision Room
- HumanWire Agent Operations

Confirmed working name: **HumanWire**.

## One-Line Summary

HumanWire is an autonomous decision-coordination system that uses Gemini and Google ADK to gather the right evidence, resolve disagreement, secure approval and availability, and produce a meeting-ready outcome while typed rules preserve human authority.

## Target User

The primary user is an executive, manager, or decision owner responsible for coordinating a consequential multi-stakeholder decision. They need progress without chasing every participant, broadcasting unnecessary requests, or trusting an opaque model to invent authority.

Secondary users are stakeholders who contribute evidence, objections, approval, or availability, and reviewers who need to understand why the workflow advanced or stopped.

## Problem

Complex decisions stall because responsibility, evidence, disagreement, authority, and availability live across different people and systems. Traditional assistants draft messages but do not own the workflow. Generic agent demos hide state changes and treat a model response as if it were authenticated evidence or approval.

HumanWire must complete the operational coordination loop autonomously while keeping every consequential transition visible, attributable, replayable, and bounded by deterministic authority rules.

## Core Workflow

1. An executive selects a launch-decision template and submits an objective, timing, role, stakeholders, and conflict policy.
2. A Google ADK coordinator creates a bounded execution plan and delegates work to specialist agents backed by Gemini 3.5+ structured outputs.
3. HumanWire contacts only the stakeholders required for the current evidence or authority gap.
4. Agents surface a disagreement, collect targeted interview answers, and distinguish asserted facts from confirmed evidence.
5. The proposal agent drafts and, when required, revises the decision proposal.
6. The authority agent records explicit approval only after the evidence gate passes.
7. The scheduling agent obtains required availability only after approval and produces a meeting package.
8. The Decision Room streams durable progress, synchronizes the graph, conversations, saved results, and lifecycle, and supports historical replay and privacy-safe exports.

## What We Are Building

- One polished Taskmaster workflow: executive objective to meeting-ready package.
- Genuine Gemini 3.5+ structured reasoning for planning, evidence interpretation, proposal language, and bounded specialist decisions.
- Genuine Google ADK orchestration with explicit coordinator and specialist-agent responsibilities.
- Cloud Run deployment for the public product and API boundary.
- Firestore persistence for run metadata, agent state, public progress snapshots, and final bindings.
- Pub/Sub dispatch for asynchronous run execution and resilient progress publication.
- The existing HumanWire typed authority engine as the deterministic gate for identity, evidence confirmation, approval, and scheduling.
- The existing navy/cyan/amber Decision Room evolved into an executive mission-control experience.
- Failure handling, idempotency, deadline enforcement, privacy projection, and replay truth.
- Reproducible local and cloud setup, automated tests, architecture diagram, deployment evidence, and a concise live demo.

## What We Are Not Building

- Real Telegram or email delivery for this entry; it distracts from the Google-native Taskmaster proof and belongs to the Caspian evidence boundary.
- Direct calendar writes; the winning proof is a verified meeting package, not a broad calendar integration.
- A general-purpose chatbot or open-ended agent marketplace.
- A full multi-tenant enterprise administration console.
- GKE, Cloud SQL, or Gemini Enterprise Agent Platform unless later evidence shows one is essential.
- Optional Veo, Lyria, or Gemma integrations before the core workflow is complete and verified.
- Multiple unrelated templates or workflows; one exceptional launch-decision path is stronger than several shallow demos.
- Claims of real people, real external delivery, or model-owned approval that the implementation cannot prove.

## Inspiration And References

- Palantir AIP: governed operational actions, explicit authority, and visible decision lineage.
- Temporal: durable workflow semantics, retries, idempotency, and recovery without double effects.
- Linear: clean hierarchy, readable state, and restrained motion rather than dashboard clutter.
- HumanWire's existing product: synchronized Decision Room, Reach, Data, replay, and typed authority boundaries.

The product should feel like an executive mission-control room with collaborative warmth: serious, calm, legible, and alive without becoming theatrical.

## Demo Path

1. Open the deployed Cloud Run URL and show the visible Gemini/ADK/Google Cloud runtime boundary.
2. Submit a launch decision objective from the HumanWire composer.
3. Show the ADK coordinator creating the plan and specialist agents beginning asynchronously.
4. Follow the live graph through outreach, disagreement, targeted evidence collection, proposal, revision, approval, availability, and scheduling.
5. Select a historical event and prove the graph, conversation, data, lifecycle, and From → To → Generated explanation agree.
6. Show the final meeting package and privacy-safe exports.
7. Show concise Google Cloud proof: Cloud Run revision, Firestore run state, Pub/Sub execution, and relevant logs without exposing credentials or private data.
8. Close on measurable operational value: one objective completed with minimum necessary human interruption and a fully inspectable authority trail.

## Submission Story

Most assistants wait for prompts or generate text. HumanWire takes responsibility for a messy, multi-step operational outcome. Gemini and ADK provide adaptive planning and specialist reasoning; Google Cloud supplies asynchronous, durable execution; HumanWire's typed rules prevent the model from fabricating identity, evidence, approval, or scheduling authority.

The entry leads with the official judging weights:

- **40% autonomous operational utility:** complete a consequential coordination workflow with minimal hand-holding.
- **30% architectural discipline:** explicit agent boundaries, durable cloud state, idempotency, privacy, failure recovery, and deterministic authority.
- **30% demo and production readiness:** a polished deployed product, reproducible repository, architecture diagram, and unmistakable Google Cloud proof.

## Scope Ruler

- Available build budget: six focused build days, with remaining calendar time reserved for review, deployment hardening, video, and submission.
- Definition of done: one deployed, reproducible, privacy-safe workflow completes reliably from objective to meeting package using the mandatory Google stack, and its live demo tells the full story in approximately four minutes.
- Scope changes require evidence that they materially improve eligibility, a judging criterion, reliability, or demo clarity.
