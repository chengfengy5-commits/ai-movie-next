// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createDemoServices } from "../shared/api/services";
import { App } from "./App";

describe("App appearance host", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.history.replaceState(null, "", "/admin");
  });

  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    window.history.replaceState(null, "", "/");
    delete document.documentElement.dataset.theme;
    document.documentElement.style.colorScheme = "";
  });

  it("mounts the global provider for the admin route and applies the restored mode", async () => {
    window.localStorage.setItem("hao-theme", "dark");
    render(<App services={createDemoServices(window.localStorage)} />);

    expect(screen.getByRole("heading", { name: "管理后台需要本地 API" })).toBeInTheDocument();
    await waitFor(() => expect(document.documentElement).toHaveAttribute("data-theme", "dark"));
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });
});
