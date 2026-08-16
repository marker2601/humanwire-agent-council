import {interpolate, OffthreadVideo, staticFile, useCurrentFrame} from "remotion";

export const AgentOverlay = ({src}: {src: string}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 12, 150, 180], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const lift = interpolate(frame, [0, 18], [24, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        position: "absolute",
        right: 34,
        bottom: 106,
        width: 500,
        height: 302,
        overflow: "hidden",
        borderRadius: 24,
        border: "1px solid rgba(49, 199, 255, 0.62)",
        background: "#061326",
        boxShadow: "0 28px 80px rgba(0,0,0,.52), 0 0 32px rgba(49,199,255,.16)",
        opacity,
        transform: `translateY(${lift}px)`,
      }}
    >
      <OffthreadVideo
        src={staticFile(src)}
        muted
        style={{width: "100%", height: "100%", objectFit: "cover"}}
      />
      <div
        style={{
          position: "absolute",
          left: 14,
          bottom: 14,
          padding: "8px 13px",
          borderRadius: 999,
          background: "rgba(2,11,24,.9)",
          color: "#dff8ff",
          fontFamily: "Inter, Segoe UI, sans-serif",
          fontSize: 20,
          fontWeight: 700,
          border: "1px solid rgba(49,199,255,.42)",
        }}
      >
        Software agents · illustrative
      </div>
    </div>
  );
};
