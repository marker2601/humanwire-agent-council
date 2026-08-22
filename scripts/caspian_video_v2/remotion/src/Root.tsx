import {Composition} from "remotion";
import {GoogleHumanWireVideo} from "./GoogleHumanWireVideo";
import {HumanWireVideo} from "./HumanWireVideo";

export const Root = () => (
  <>
    <Composition
      id="HumanWireProfessional"
      component={HumanWireVideo}
      durationInFrames={2400}
      fps={30}
      width={1920}
      height={1080}
    />
    <Composition
      id="HumanWireGoogleSubmission"
      component={GoogleHumanWireVideo}
      durationInFrames={3360}
      fps={30}
      width={1920}
      height={1080}
    />
  </>
);
