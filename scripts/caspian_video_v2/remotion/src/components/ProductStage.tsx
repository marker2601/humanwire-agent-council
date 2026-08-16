import {
  AbsoluteFill,
  Easing,
  interpolate,
  OffthreadVideo,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import {TruthBadge} from "./TruthBadge";

type ProductStageProps = {
  src: string;
  sourceStartSeconds: number;
  title: string;
  eyebrow: string;
  accent?: string;
  truthCopy: string;
  children?: React.ReactNode;
};

export const ProductStage = ({
  src,
  sourceStartSeconds,
  title,
  eyebrow,
  accent = "#31c7ff",
  truthCopy,
  children,
}: ProductStageProps) => {
  const frame = useCurrentFrame();
  const {durationInFrames, fps} = useVideoConfig();
  const scale = interpolate(frame, [0, durationInFrames], [1.008, 1], {
    easing: Easing.out(Easing.quad),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{background: "#020b18"}}>
      <div
        style={{
          position: "absolute",
          inset: 0,
          overflow: "hidden",
          minWidth: 1382,
          minHeight: 778,
        }}
      >
        <OffthreadVideo
          src={staticFile(src)}
          startFrom={Math.round(sourceStartSeconds * fps)}
          muted
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            transform: `scale(${scale})`,
            transformOrigin: "50% 46%",
          }}
        />
      </div>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "linear-gradient(180deg, rgba(1,7,16,.24) 0%, transparent 17%, transparent 76%, rgba(1,7,16,.34) 100%)",
          pointerEvents: "none",
        }}
      />
      <div
        style={{
          position: "absolute",
          left: 28,
          top: 24,
          display: "flex",
          alignItems: "center",
          gap: 13,
          maxWidth: 880,
          padding: "11px 17px",
          borderRadius: 14,
          border: `1px solid ${accent}55`,
          background: "rgba(3, 15, 29, 0.9)",
          boxShadow: "0 16px 44px rgba(0,0,0,.28)",
          fontFamily: "Inter, Segoe UI, sans-serif",
        }}
      >
        <span
          style={{
            color: accent,
            fontSize: 19,
            fontWeight: 800,
            letterSpacing: 1.4,
            textTransform: "uppercase",
          }}
        >
          {eyebrow}
        </span>
        <span style={{width: 1, height: 27, background: `${accent}66`}} />
        <strong style={{color: "#f4f9ff", fontSize: 25, fontWeight: 700}}>
          {title}
        </strong>
      </div>
      <TruthBadge copy={truthCopy} />
      {children}
    </AbsoluteFill>
  );
};
