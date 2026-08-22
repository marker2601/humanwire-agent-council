import {interpolate, useCurrentFrame} from "remotion";
import captionCues from "../../../../../submission/all-things-agentic-caption-cues.json";

const cues: ReadonlyArray<readonly [number, number, string]> = captionCues.map(
  ([start, end, copy]) => [Number(start), Number(end), String(copy)] as const,
);

export const GoogleCaptionLayer = () => {
  const frame = useCurrentFrame();
  const cue = cues.find(([start, end]) => frame >= start && frame < end);
  if (!cue) return null;
  const [start, end, copy] = cue;
  const opacity = interpolate(frame, [start, start + 5, end - 5, end], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div style={{position: "absolute", left: 42, bottom: 86, maxWidth: 980, padding: "13px 19px", borderRadius: 15, background: "rgba(2,11,24,.94)", border: "1px solid rgba(153,211,255,.34)", boxShadow: "0 18px 54px rgba(0,0,0,.44)", color: "#f7fbff", fontFamily: "Inter, Segoe UI, sans-serif", fontSize: 34, fontWeight: 680, lineHeight: 1.2, whiteSpace: "pre-line", opacity}}>{copy}</div>
  );
};
