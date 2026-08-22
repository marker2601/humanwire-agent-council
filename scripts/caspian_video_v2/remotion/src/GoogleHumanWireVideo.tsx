import {
  AbsoluteFill,
  Audio,
  interpolate,
  OffthreadVideo,
  Sequence,
  staticFile,
  useCurrentFrame,
} from "remotion";
import {GoogleCaptionLayer} from "./components/GoogleCaptionLayer";
import {TruthBadge} from "./components/TruthBadge";

const TRUTH_COPY = "Live Google run · no external stakeholder messages";
const font = "Inter, Segoe UI, sans-serif";

const fade = (frame: number, duration: number) =>
  interpolate(frame, [0, 10, duration - 10, duration], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

const Hook = () => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [8, 26], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const rise = interpolate(frame, [8, 28], [24, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill style={{background: "#020a16", opacity: fade(frame, 180)}}>
      <OffthreadVideo
        src={staticFile("google/veo-google-hook.mp4")}
        muted
        style={{width: "100%", height: "100%", objectFit: "cover"}}
      />
      <AbsoluteFill style={{background: "linear-gradient(90deg, rgba(1,8,19,.08), rgba(1,8,19,.97) 76%)"}} />
      <div style={{position: "absolute", right: 92, top: 205, width: 840, color: "white", fontFamily: font, opacity, transform: `translateY(${rise}px)`}}>
        <div style={{fontSize: 25, color: "#40d5ff", fontWeight: 800, letterSpacing: 5}}>HUMANWIRE</div>
        <div style={{fontSize: 72, lineHeight: 1.02, fontWeight: 850, marginTop: 22}}>Big decisions need<br />more than AI.</div>
        <div style={{fontSize: 29, color: "#c2d7e9", marginTop: 28, lineHeight: 1.35}}>Evidence. Accountable voices.<br />Explicit human authority.</div>
      </div>
      <div style={{position: "absolute", left: 42, top: 34, color: "#dff7ff", background: "rgba(2,11,24,.86)", border: "1px solid rgba(49,199,255,.48)", borderRadius: 999, padding: "10px 16px", fontFamily: font, fontSize: 20, fontWeight: 700}}>Google Veo 3.1 visual guide</div>
    </AbsoluteFill>
  );
};

const Architecture = () => {
  const frame = useCurrentFrame();
  const scale = interpolate(frame, [0, 180], [1.045, 1.01], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill style={{background: "radial-gradient(circle at 50% 45%, #0b3150, #020a16 62%)", color: "white", fontFamily: font, opacity: fade(frame, 180)}}>
      <div style={{position: "absolute", left: 74, top: 42, zIndex: 2}}>
        <div style={{fontSize: 23, color: "#40d5ff", fontWeight: 800, letterSpacing: 4}}>THE DECISION CONTROL PLANE</div>
        <div style={{fontSize: 48, fontWeight: 820, marginTop: 8}}>Models propose. Human authority stays visible.</div>
      </div>
      <img src={staticFile("google/architecture.png")} style={{position: "absolute", left: 88, top: 126, width: 1744, height: 920, objectFit: "contain", transform: `scale(${scale})`}} />
    </AbsoluteFill>
  );
};

type ProductChapter = {
  start: number;
  end: number;
  kicker: string;
  title: string;
  accent: string;
  zoom: number;
  origin: string;
};

const productChapters: readonly ProductChapter[] = [
  {start: 0, end: 390, kicker: "01 · DEMO MISSION", title: "One agenda starts durable work", accent: "#40d5ff", zoom: 1.018, origin: "50% 40%"},
  {start: 390, end: 990, kicker: "02 · SPECIALIST COUNCIL", title: "Seven bounded agents work in parallel", accent: "#a78bfa", zoom: 1.035, origin: "50% 42%"},
  {start: 990, end: 1530, kicker: "03 · STAKEHOLDER VOICES", title: "Eight roles contribute distinct evidence", accent: "#fb7185", zoom: 1.04, origin: "54% 49%"},
  {start: 1530, end: 1980, kicker: "04 · DECISION BRIEF", title: "Facts, inferences, and challenges stay separate", accent: "#fbbf24", zoom: 1.035, origin: "52% 50%"},
  {start: 1980, end: 2532, kicker: "05 · AUDIT TRAIL", title: "The saved record survives refresh", accent: "#53e889", zoom: 1.028, origin: "51% 51%"},
] as const;

const activeChapter = (frame: number) =>
  productChapters.find((chapter) => frame >= chapter.start && frame < chapter.end) ?? productChapters[productChapters.length - 1];

export const ProductFocus = () => {
  const frame = useCurrentFrame();
  const chapter = activeChapter(frame);
  const local = frame - chapter.start;
  const duration = chapter.end - chapter.start;
  const zoom = interpolate(local, [0, 24, duration - 18, duration], [1, chapter.zoom, chapter.zoom, 1.005], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div style={{position: "absolute", inset: 14, overflow: "hidden", borderRadius: 22, border: "1px solid rgba(82,205,255,.32)", boxShadow: "0 28px 100px rgba(0,0,0,.48)", background: "#020a16"}}>
      <OffthreadVideo
        src={staticFile("google/raw/decisionos-release-00040.mp4")}
        muted
        style={{width: "100%", height: "100%", objectFit: "cover", transform: `scale(${zoom})`, transformOrigin: chapter.origin}}
      />
      <AbsoluteFill style={{boxShadow: "inset 0 0 90px rgba(1,8,19,.28)", pointerEvents: "none"}} />
    </div>
  );
};

export const ChapterPulse = () => {
  const frame = useCurrentFrame();
  const chapter = activeChapter(frame);
  const local = frame - chapter.start;
  const opacity = interpolate(local, [0, 10, 70, 90], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const translate = interpolate(local, [0, 18], [16, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div style={{position: "absolute", left: 38, top: 92, width: 760, padding: "18px 22px", borderRadius: 18, border: `1px solid ${chapter.accent}66`, background: "linear-gradient(135deg, rgba(2,11,24,.96), rgba(6,31,53,.89))", boxShadow: "0 22px 70px rgba(0,0,0,.42)", fontFamily: font, color: "white", opacity, transform: `translateY(${translate}px)`}}>
      <div style={{fontSize: 19, color: chapter.accent, fontWeight: 850, letterSpacing: 3}}>{chapter.kicker}</div>
      <div style={{fontSize: 35, fontWeight: 820, marginTop: 6, lineHeight: 1.14}}>{chapter.title}</div>
    </div>
  );
};

const LiveRun = () => (
  <AbsoluteFill style={{background: "radial-gradient(circle at 50% 20%, #0b3150, #020a16 68%)"}}>
    <ProductFocus />
    <div style={{position: "absolute", left: 34, top: 28, display: "flex", gap: 11, alignItems: "center", border: "1px solid rgba(62,213,255,.48)", background: "rgba(2,11,24,.94)", borderRadius: 999, padding: "9px 15px", color: "white", fontFamily: font}}>
      <span style={{width: 10, height: 10, borderRadius: "50%", background: "#53e889", boxShadow: "0 0 16px #53e889"}} />
      <strong style={{fontSize: 21}}>RECORDED LIVE · GOOGLE CLOUD</strong>
      <span style={{fontSize: 19, color: "#a9c3db"}}>Gemini 3.5 Flash · Google ADK 2.7</span>
    </div>
    <ChapterPulse />
    <TruthBadge copy={TRUTH_COPY} />
  </AbsoluteFill>
);

const CloudProof = () => {
  const frame = useCurrentFrame();
  const rise = interpolate(frame, [0, 22], [18, 0], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  const cards = [
    ["Authenticated edge", "Firebase Auth + App Check", "Google sign-in · App Check monitored"],
    ["DecisionOS service", "humanwire-decisionos-00040-g92", "Cloud Run · 100% production traffic"],
    ["Durable record", "Firestore durable state", "Mission · evidence · Council · audit"],
    ["Agent runtime", "Google ADK 2.7 · Gemini 3.5 Flash", "Vertex AI · bounded specialist roles"],
  ] as const;
  return (
    <AbsoluteFill style={{background: "radial-gradient(circle at 50% 35%, #0a3854, #020a16 65%)", color: "white", fontFamily: font, padding: "58px 78px", opacity: fade(frame, 270)}}>
      <div style={{transform: `translateY(${rise}px)`}}>
        <div style={{fontSize: 23, color: "#40d5ff", fontWeight: 850, letterSpacing: 4}}>DEPLOYED GOOGLE CLOUD PROOF</div>
        <div style={{fontSize: 53, fontWeight: 850, marginTop: 10}}>Authenticated. Durable. Human-gated.</div>
        <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginTop: 38}}>
          {cards.map(([title, proof, copy]) => <div key={title} style={{border: "1px solid rgba(64,213,255,.4)", background: "rgba(7,24,43,.94)", borderRadius: 20, padding: "22px 26px"}}><div style={{fontSize: 27, fontWeight: 820}}>{title}</div><div style={{fontSize: 22, color: "#53e889", marginTop: 8}}>{proof}</div><div style={{fontSize: 21, color: "#c3d7e8", marginTop: 10}}>{copy}</div></div>)}
        </div>
        <div style={{marginTop: 22, borderRadius: 16, background: "rgba(2,10,22,.84)", padding: "16px 22px", fontSize: 22, color: "#d6e5f3"}}>humanwire-decisionos-wjjhjrgnyq-uc.a.run.app · explicit human approval required</div>
      </div>
    </AbsoluteFill>
  );
};

const Closing = () => {
  const frame = useCurrentFrame();
  const scale = interpolate(frame, [0, 198], [0.96, 1.018], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  return (
    <AbsoluteFill style={{background: "radial-gradient(circle at 50% 42%, rgba(28,151,219,.34), transparent 46%), #020a16", color: "white", fontFamily: font, alignItems: "center", justifyContent: "center", textAlign: "center", opacity: fade(frame, 198)}}>
      <div style={{transform: `scale(${scale})`}}>
        <div style={{fontSize: 27, color: "#40d5ff", fontWeight: 850, letterSpacing: 6}}>HUMANWIRE · TASKMASTER</div>
        <div style={{fontSize: 70, fontWeight: 850, marginTop: 18}}>Less meeting time. More evidence.</div>
        <div style={{fontSize: 32, color: "#d8e7f5", marginTop: 18}}>Human authority stays visible.</div>
        <div style={{fontSize: 24, color: "#8fb5d1", marginTop: 22}}>humanwire-decisionos-wjjhjrgnyq-uc.a.run.app</div>
      </div>
    </AbsoluteFill>
  );
};

const narration = [
  [0, "00-hook.mp3"],
  [180, "01-architecture.mp3"],
  [360, "02-start.mp3"],
  [750, "03-analysis.mp3"],
  [1350, "04-stakeholders.mp3"],
  [1890, "05-decision.mp3"],
  [2340, "06-audit.mp3"],
  [2892, "07-cloud.mp3"],
  [3162, "08-close.mp3"],
] as const;

export const GoogleHumanWireVideo = () => (
  <AbsoluteFill style={{background: "#020a16"}}>
    <Audio src={staticFile("google/audio/lyria-score-225.mp3")} volume={0.12} />
    <Sequence from={0} durationInFrames={180}><Hook /></Sequence>
    <Sequence from={180} durationInFrames={180}><Architecture /></Sequence>
    <Sequence from={360} durationInFrames={2532}><LiveRun /></Sequence>
    <Sequence from={2892} durationInFrames={270}><CloudProof /></Sequence>
    <Sequence from={3162} durationInFrames={198}><Closing /></Sequence>
    {narration.map(([from, name]) => <Sequence key={name} from={from}><Audio src={staticFile(`google/audio/${name}`)} volume={1} /></Sequence>)}
    <GoogleCaptionLayer />
  </AbsoluteFill>
);
