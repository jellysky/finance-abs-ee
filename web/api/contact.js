// Vercel serverless function: POST /api/contact
// Verifies the Cloudflare Turnstile token, then sends the message via Resend to peter@serention.com.
//
// Required environment variables (set in the Vercel project, never commit them):
//   TURNSTILE_SECRET_KEY   – Cloudflare Turnstile secret (pairs with the site key in contact.html)
//   RESEND_API_KEY         – Resend API key
//   CONTACT_TO             – (optional) recipient; defaults to peter@serention.com
//   CONTACT_FROM           – (optional) verified Resend sender; defaults to "Serention <noreply@serention.com>"
//
// No npm dependencies — uses the global fetch available on Vercel's Node 18+ runtime.

const TO = process.env.CONTACT_TO || "peter@serention.com";
const FROM = process.env.CONTACT_FROM || "Serention <noreply@serention.com>";

function readBody(req) {
  if (req.body && typeof req.body === "object") return Promise.resolve(req.body);
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (c) => (raw += c));
    req.on("end", () => { try { resolve(JSON.parse(raw || "{}")); } catch { resolve({}); } });
  });
}

module.exports = async function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).json({ ok: false, error: "Method not allowed" });
  }
  if (!process.env.TURNSTILE_SECRET_KEY || !process.env.RESEND_API_KEY) {
    return res.status(500).json({ ok: false, error: "Contact form is not configured." });
  }

  const { name, email, message, token } = await readBody(req);
  if (!name || !email || !message || !token) {
    return res.status(400).json({ ok: false, error: "Missing required fields." });
  }
  if (typeof message === "string" && message.length > 5000) {
    return res.status(400).json({ ok: false, error: "Message too long." });
  }

  // 1) Verify the Turnstile token with Cloudflare.
  try {
    const params = new URLSearchParams();
    params.append("secret", process.env.TURNSTILE_SECRET_KEY);
    params.append("response", token);
    const ip = req.headers["cf-connecting-ip"] || req.headers["x-forwarded-for"];
    if (ip) params.append("remoteip", String(ip).split(",")[0].trim());
    const vr = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
      method: "POST", body: params,
    });
    const verdict = await vr.json();
    if (!verdict.success) {
      return res.status(400).json({ ok: false, error: "CAPTCHA verification failed." });
    }
  } catch {
    return res.status(502).json({ ok: false, error: "Could not verify CAPTCHA. Try again." });
  }

  // 2) Send the email via Resend.
  try {
    const safe = (s) => String(s).replace(/[<>]/g, "");
    const er = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${process.env.RESEND_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: FROM,
        to: [TO],
        reply_to: email,
        subject: `Serention contact — ${safe(name)}`,
        text: `From: ${safe(name)} <${safe(email)}>\n\n${message}`,
      }),
    });
    if (!er.ok) {
      const detail = await er.text().catch(() => "");
      return res.status(502).json({ ok: false, error: "Could not send the message.", detail });
    }
  } catch {
    return res.status(502).json({ ok: false, error: "Could not send the message. Try again." });
  }

  return res.status(200).json({ ok: true });
}
