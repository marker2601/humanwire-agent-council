import {interpolate, useCurrentFrame} from "remotion";

const cues = [
  [0, 210, "Important decisions fail when objection,\nevidence, and authority never meet."],
  [210, 255, "HumanWire brings them into one workflow."],
  [255, 378, "A manager asks for tomorrow's\nlaunch decision."],
  [378, 498, "HumanWire selects the minimum\nnecessary stakeholder path."],
  [498, 636, "One Caspian gateway coordinates\nthe selected roles."],
  [636, 720, "Anika flags a rollback risk."],
  [720, 795, "HumanWire opens one focused interview."],
  [795, 885, "Her answer becomes shareable evidence\nafter confirmation."],
  [885, 975, "Confirmed evidence revises the proposal."],
  [975, 1065, "Sofia approves the revised plan."],
  [1065, 1221, "Only then does Daniel\nprovide availability."],
  [1221, 1371, "HumanWire builds a\nmeeting-ready result."],
  [1371, 1512, "Replay preserves every saved event."],
  [1512, 1755, "JSON and CSV expose matching records."],
  [1755, 2052, "Caspian is the configurable,\nconsented-delivery boundary."],
  [2052, 2400, "Standard agents · no external messages\nHumanWire · Decision-ready"],
] as const;

export const CaptionLayer = () => {
  const frame = useCurrentFrame();
  const cue = cues.find(([start, end]) => frame >= start && frame < end);
  if (!cue) return null;
  const [start, end, copy] = cue;
  const opacity = interpolate(frame, [start, start + 7, end - 7, end], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        position: "absolute",
        left: 40,
        bottom: 94,
        maxWidth: 760,
        padding: "14px 20px",
        borderRadius: 16,
        background: "rgba(2, 11, 24, 0.9)",
        border: "1px solid rgba(153, 211, 255, 0.3)",
        boxShadow: "0 18px 54px rgba(0,0,0,.42)",
        color: "#f7fbff",
        fontFamily: "Inter, Segoe UI, sans-serif",
        fontSize: 32,
        fontWeight: 650,
        lineHeight: 1.25,
        whiteSpace: "pre-line",
        letterSpacing: -0.35,
        opacity,
        pointerEvents: "none",
      }}
    >
      {copy}
    </div>
  );
};
