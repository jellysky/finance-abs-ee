// Contact form → POSTs to the /api/contact serverless function, which verifies the
// Cloudflare Turnstile token server-side and sends the email via Resend.
const form = document.getElementById("contactForm");
const note = document.getElementById("formnote");
const btn = document.getElementById("send");

function setNote(msg, ok) { note.textContent = msg; note.className = "formnote " + (ok ? "ok" : "err"); }

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("name").value.trim();
  const email = document.getElementById("email").value.trim();
  const message = document.getElementById("message").value.trim();
  const token = (window.turnstile && window.turnstile.getResponse()) ||
                (form.querySelector('[name="cf-turnstile-response"]') || {}).value || "";

  if (!name || !email || !message) { setNote("Please fill in name, email, and message.", false); return; }
  if (!token) { setNote("Please complete the CAPTCHA.", false); return; }

  btn.disabled = true; setNote("Sending…", true);
  try {
    const res = await fetch("/api/contact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, email, message, token }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.ok) {
      setNote("Thanks — your message was sent. We'll be in touch.", true);
      form.reset();
    } else {
      setNote(data.error || "Sorry, something went wrong. Please try again or email peter@serention.com.", false);
    }
  } catch (_) {
    setNote("Network error. Please try again or email peter@serention.com.", false);
  } finally {
    btn.disabled = false;
    if (window.turnstile) window.turnstile.reset();
  }
});
