import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("keeps the paper-trading banner visible when the API is down", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<App />);
    expect(screen.getByTestId("paper-banner")).toHaveTextContent(/paper trading only/i);
    expect(await screen.findByText(/API unreachable: Failed to fetch/)).toBeInTheDocument();
  });
});
