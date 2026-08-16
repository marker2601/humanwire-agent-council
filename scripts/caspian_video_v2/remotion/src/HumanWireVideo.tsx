import {
  AbsoluteFill,
  Audio,
  interpolate,
  OffthreadVideo,
  Sequence,
  staticFile,
  useCurrentFrame,
} from "remotion";
import {AgentOverlay} from "./components/AgentOverlay";
import {CaptionLayer} from "./components/CaptionLayer";
import {ProductStage} from "./components/ProductStage";

const TRUTH_COPY = "Standard agents · no external messages";

const Hook = () => {
  const frame = useCurrentFrame();
  const titleOpacity = interpolate(frame, [12, 28], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const titleX = interpolate(frame, [12, 34], [40, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{background: "#020a16"}}>
      <OffthreadVideo
        src={staticFile("generated/visual-guide.mp4")}
        muted
        style={{width: "100%", height: "100%", objectFit: "cover"}}
      />
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(90deg, rgba(1,8,19,.04) 18%, rgba(1,8,19,.56) 53%, rgba(1,8,19,.98) 100%)",
        }}
      />
      <div
        style={{
          position: "absolute",
          left: 1030,
          top: 244,
          width: 760,
          color: "white",
          fontFamily: "Inter, Segoe UI, sans-serif",
          opacity: titleOpacity,
          transform: `translateX(${titleX}px)`,
        }}
      >
        <div
          style={{
            color: "#3dd5ff",
            fontSize: 24,
            fontWeight: 800,
            letterSpacing: 2.6,
            textTransform: "uppercase",
            marginBottom: 24,
          }}
        >
          HumanWire
        </div>
        <div style={{fontSize: 66, lineHeight: 1.04, fontWeight: 780, letterSpacing: -2.4}}>
          Decisions move when evidence and authority meet.
        </div>
        <div
          style={{
            marginTop: 32,
            color: "#b8cce0",
            fontSize: 27,
            lineHeight: 1.45,
            maxWidth: 640,
          }}
        >
          One accountable path from request to meeting-ready result.
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          left: 44,
          top: 34,
          padding: "10px 16px",
          borderRadius: 999,
          border: "1px solid rgba(49,199,255,.48)",
          background: "rgba(2,11,24,.82)",
          color: "#dff7ff",
          fontFamily: "Inter, Segoe UI, sans-serif",
          fontSize: 21,
          fontWeight: 700,
        }}
      >
        Fictional visual guide
      </div>
    </AbsoluteFill>
  );
};

const Closing = () => {
  const frame = useCurrentFrame();
  const glow = interpolate(frame, [0, 150], [0.35, 0.7]);
  return (
    <AbsoluteFill
      style={{
        background:
          "radial-gradient(circle at 50% 38%, rgba(28,151,219,.28), transparent 43%), #020a16",
        color: "white",
        fontFamily: "Inter, Segoe UI, sans-serif",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          position: "absolute",
          width: 520,
          height: 520,
          borderRadius: "50%",
          border: "1px solid rgba(49,199,255,.23)",
          boxShadow: `0 0 130px rgba(49,199,255,${glow})`,
        }}
      />
      <div style={{position: "relative", textAlign: "center", width: 1320}}>
        <div style={{fontSize: 32, color: "#3dd5ff", fontWeight: 800, letterSpacing: 5}}>
          HUMANWIRE
        </div>
        <div style={{fontSize: 74, lineHeight: 1.08, fontWeight: 790, marginTop: 24}}>
          One mandate. The right conversations.
        </div>
        <div style={{fontSize: 38, color: "#c4d5e6", marginTop: 18}}>
          A decision-ready meeting built on confirmed evidence.
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            gap: 24,
            marginTop: 54,
            fontSize: 25,
            color: "#a8c2d9",
          }}
        >
          <span>secondsignal.vercel.app</span>
          <span style={{color: "#3dd5ff"}}>·</span>
          <span>github.com/marker2601/humanwire</span>
        </div>
      </div>
    </AbsoluteFill>
  );
};

export const HumanWireVideo = () => (
  <AbsoluteFill style={{background: "#020a16"}}>
    <Sequence from={0} durationInFrames={180}>
      <Hook />
    </Sequence>
    <Sequence from={180} durationInFrames={210}>
      <ProductStage src="raw/public-product-clean.webm" sourceStartSeconds={0.95} eyebrow="Request" title="One clear decision mandate" truthCopy={TRUTH_COPY} />
    </Sequence>
    <Sequence from={390} durationInFrames={240}>
      <ProductStage src="raw/public-product-clean.webm" sourceStartSeconds={7.25} eyebrow="Minimum path" title="Only the roles this decision needs" truthCopy={TRUTH_COPY}>
        <AgentOverlay src="generated/agent-flow.mp4" />
      </ProductStage>
    </Sequence>
    <Sequence from={630} durationInFrames={420}>
      <ProductStage src="raw/public-product-clean.webm" sourceStartSeconds={19.47} eyebrow="Resolve" title="Conflict → interview → confirmed evidence" accent="#f9bd4a" truthCopy={TRUTH_COPY} />
    </Sequence>
    <Sequence from={1050} durationInFrames={330}>
      <ProductStage src="raw/public-product-clean.webm" sourceStartSeconds={33.5} eyebrow="Approve & schedule" title="Revision → approval → availability" accent="#53e889" truthCopy={TRUTH_COPY} />
    </Sequence>
    <Sequence from={1380} durationInFrames={360}>
      <ProductStage src="raw/public-product-clean.webm" sourceStartSeconds={47.53} eyebrow="Replay" title="Result, replay, and matching exports" truthCopy={TRUTH_COPY} />
    </Sequence>
    <Sequence from={1740} durationInFrames={300}>
      <ProductStage src="raw/public-product-clean.webm" sourceStartSeconds={63.23} eyebrow="Caspian gateway" title="Channel-neutral, consented delivery boundary" accent="#a78bfa" truthCopy={TRUTH_COPY} />
    </Sequence>
    <Sequence from={2040} durationInFrames={120}>
      <ProductStage src="raw/public-product-clean.webm" sourceStartSeconds={47.53} eyebrow="Decision-ready" title="Meeting package built on confirmed evidence" accent="#53e889" truthCopy={TRUTH_COPY} />
    </Sequence>
    <Sequence from={2160} durationInFrames={240}>
      <Closing />
    </Sequence>
    <Sequence from={9}>
      <Audio src={staticFile("generated/narration-paced.mp3")} volume={0.96} />
    </Sequence>
    <CaptionLayer />
  </AbsoluteFill>
);
