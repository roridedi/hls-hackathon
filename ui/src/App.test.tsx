import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import { ApiClient } from "./api";
import { SourceDialog } from "./SourceDialog";

describe("reviewer workbench", () => {
  it("uses a real sign in form and renders all queues", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const body = url.endsWith("/health") ? { environment: "development" } :
        url.endsWith("/me") ? { actor_id: "synthetic-physician", roles: ["physician_reviewer"] } :
          { administrative: [], nurse: [], physician: [], peer_to_peer: [], letters: [] };
      return new Response(JSON.stringify(body), { status: 200 });
    }));
    render(<App />);
    const signIn = await screen.findByRole("button", { name: "Sign in to synthetic workbench" });
    await userEvent.click(signIn);
    expect(await screen.findByRole("navigation", { name: "Queue selection" })).toBeVisible();
    expect(screen.getByRole("button", { name: /Letters pending release/ })).toBeVisible();
    expect(screen.getByText("Evidence first. Humans decide.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Skip to workbench" })).toHaveAttribute("href", "#main");
  });

  it("surfaces API errors without echoing request content", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "A licensed reviewer is required." }), { status: 403 })));
    await expect(new ApiClient("dev-nurse").request("/cases/test/actions/determine", { outcome: "DENIED" }))
      .rejects.toThrow("A licensed reviewer is required.");
  });

  it("highlights exactly the cited span without interpreting source HTML", () => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
    render(<SourceDialog source={{
      citation: { source_type: "DOCUMENT", source_id: "synthetic-note", page: 1, char_start: 7, char_end: 15, resource_path: null },
      text: "Before A1C: 8.2 <script>bad</script>", highlight_start: 7, highlight_end: 15,
    }} close={() => undefined} />);
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(document.querySelector("mark")).toHaveTextContent("A1C: 8.2");
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByRole("button", { name: "Close source" })).toBeVisible();
  });
});
