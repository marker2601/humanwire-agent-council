import {Composition} from "remotion";
import {HumanWireVideo} from "./HumanWireVideo";

export const Root = () => (
  <Composition
    id="HumanWireProfessional"
    component={HumanWireVideo}
    durationInFrames={2400}
    fps={30}
    width={1920}
    height={1080}
  />
);
