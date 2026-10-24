// Add to fal-call-center (client side) so the physical phone drives the call.
// The hook-switch box types F8 on pickup and F9 on hangup.
// Point the selectors at the real Call / End buttons (or call your start/stop functions directly).
window.addEventListener("keydown", (e) => {
  if (e.repeat) return;
  if (e.key === "F8") {
    e.preventDefault();
    document.querySelector("[data-call-start]")?.click();
  }
  if (e.key === "F9") {
    e.preventDefault();
    document.querySelector("[data-call-end]")?.click();
  }
});

// Optional: in booth mode, hide the on-screen buttons and prompt people to pick up.
if (new URLSearchParams(location.search).has("booth")) {
  document.documentElement.classList.add("booth");
}
// CSS: .booth [data-call-start], .booth [data-call-end] { display: none; }
