import {interpolate, useCurrentFrame} from "remotion";

export const TruthBadge = ({copy}: {copy: string}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 10], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        position: "absolute",
        right: 28,
        top: 24,
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "11px 18px",
        border: "1px solid rgba(52, 211, 153, 0.38)",
        borderRadius: 999,
        background: "rgba(3, 15, 29, 0.88)",
        boxShadow: "0 14px 40px rgba(0, 0, 0, 0.28)",
        color: "#d9fbe8",
        fontFamily: "Inter, Segoe UI, sans-serif",
        fontSize: 22,
        fontWeight: 650,
        letterSpacing: 0.1,
        opacity,
      }}
    >
      <span
        style={{
          width: 10,
          height: 10,
          borderRadius: "50%",
          background: "#53e889",
          boxShadow: "0 0 18px rgba(83, 232, 137, 0.8)",
        }}
      />
      Recorded product · {copy}
    </div>
  );
};
