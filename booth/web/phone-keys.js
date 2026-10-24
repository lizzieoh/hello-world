// Add to fal-call-center (client side) so the physical phone drives the app.
// The phone's Pico types:
//   F8  = handset lifted   -> start a fresh session and start listening (this replaces "press to speak")
//   F9  = handset hung up  -> stop listening / stop audio, but leave the image on screen
//   0-9 * #                -> keypad digits ("2" = audio generation, "3" = regenerate)
// Point the selectors at the real buttons, or call your functions directly.
const press = (selector) => document.querySelector(selector)?.click();

window.addEventListener("keydown", (e) => {
  if (e.repeat) return;
  switch (e.key) {
    case "F8":
      e.preventDefault();
      press("[data-reset]");   // back to a fresh state for the new visitor
      press("[data-speak]");   // the existing "press to speak" button
      break;
    case "F9":
      e.preventDefault();
      press("[data-stop]");    // stop mic + any playing audio; keep the image
      break;
    case "#":
      press("[data-speak-done]"); // optional: "press pound when you're done speaking"
      break;
    case "2":
      press("[data-generate-audio]");
      break;
    case "3":
      press("[data-regenerate]");
      break;
  }
});

// In booth mode, hide the on-screen buttons and say "pick up the phone" instead.
if (new URLSearchParams(location.search).has("booth")) {
  document.documentElement.classList.add("booth");
}
// CSS: .booth [data-speak], .booth [data-generate-audio], .booth [data-regenerate] { display: none; }
