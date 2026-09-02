import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "../src/App";

describe("App", () => {
  it("communicates the stage-zero safety boundary", () => {
    render(<App />);

    expect(
      screen.getByRole("heading", { name: "AI-SRE Copilot" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Evidence First")).toBeInTheDocument();
    expect(screen.getByText("Read-only by Default")).toBeInTheDocument();
    expect(screen.getByText("Bounded Autonomy")).toBeInTheDocument();
  });
});
