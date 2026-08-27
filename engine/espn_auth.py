"""
ESPN sign-in helpers.

Two ways to obtain an ESPN session without asking the user to copy cookies out
of DevTools:

1. `detect_browser_cookies()` — read the ESPN cookies straight out of the local
   browser profile. Zero clicks when it works. Chrome 127+ encrypts its cookie
   store with app-bound encryption on Windows, which defeats this for Chrome
   specifically, so Edge and Firefox are tried too.

2. `BrowserLogin` — open ESPN's real login page in a real browser window and
   wait for the session cookies to appear. The user types their password into
   ESPN's own page; this app never sees it. Handles 2FA and captcha for free,
   because ESPN is handling them.

Both return the same pair: (swid, espn_s2).
"""

import threading
import time

# ESPN sets its auth cookies on the parent domain, and the two we need are:
_COOKIE_DOMAIN = ".espn.com"
_SWID = "SWID"
_S2 = "espn_s2"

# Hitting a page that requires a team forces the login redirect when logged out,
# and lands on a real page (setting cookies) when already logged in.
_LOGIN_TARGET = "https://fantasy.espn.com/football/team"


def _pair_from(mapping):
    """Return (swid, espn_s2) from a name->value mapping, or None if incomplete."""
    swid = mapping.get(_SWID)
    s2 = mapping.get(_S2)
    if swid and s2:
        return swid, s2
    return None


# ── 1. Silent detection ────────────────────────────────────────────────────────

def detect_browser_cookies():
    """Try to read an existing ESPN session from local browser profiles.

    Returns (swid, espn_s2) or None. Never raises — every failure mode here
    (library missing, profile locked, encryption unsupported) just means "fall
    back to the login window".
    """
    try:
        import browser_cookie3
    except ImportError:
        return None

    loaders = []
    for name in ("chrome", "edge", "firefox", "brave", "chromium", "opera"):
        fn = getattr(browser_cookie3, name, None)
        if fn:
            loaders.append((name, fn))

    for _name, loader in loaders:
        try:
            jar = loader(domain_name=_COOKIE_DOMAIN)
        except Exception:
            # Locked profile, unsupported encryption, no such browser — move on.
            continue
        found = {c.name: c.value for c in jar if c.name in (_SWID, _S2)}
        pair = _pair_from(found)
        if pair:
            return pair
    return None


# ── 2. Real login window ───────────────────────────────────────────────────────

class BrowserLogin:
    """Runs an ESPN login in a visible browser window on a background thread.

    Kept off the request thread so the UI can poll for progress instead of
    holding an HTTP connection open for however long the user takes to type a
    password and clear 2FA.
    """

    # Prefer the user's real Chrome/Edge over a bundled Chromium: no 150MB
    # download, and their existing profile may already be signed in.
    _CHANNELS = ("chrome", "msedge", None)

    def __init__(self, timeout_seconds=300, poll_seconds=1.5):
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._thread = None
        self.state = "idle"        # idle | waiting | success | error | cancelled
        self.message = ""
        self.result = None         # (swid, espn_s2)

    # ── public API ────────────────────────────────────────────────────────────

    def status(self):
        with self._lock:
            return {
                "state": self.state,
                "message": self.message,
                "has_result": self.result is not None,
            }

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running():
            return False
        self._set("waiting", "Opening the ESPN sign-in window...")
        self.result = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def cancel(self):
        self._set("cancelled", "Sign-in cancelled.")

    # ── internals ─────────────────────────────────────────────────────────────

    def _set(self, state, message):
        with self._lock:
            self.state = state
            self.message = message

    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._set(
                "error",
                "Playwright isn't installed. Run: pip install playwright  "
                "(then re-run the launcher), or use the manual cookie option.",
            )
            return

        try:
            with sync_playwright() as pw:
                browser = self._launch(pw)
                if browser is None:
                    self._set(
                        "error",
                        "Couldn't open a browser window. Make sure Chrome or Edge "
                        "is installed, or use the manual cookie option.",
                    )
                    return

                context = browser.new_context()
                page = context.new_page()
                page.goto(_LOGIN_TARGET, wait_until="domcontentloaded")
                self._set(
                    "waiting",
                    "Sign in to ESPN in the window that just opened. "
                    "This window closes by itself when you're done.",
                )

                pair = self._await_cookies(context)
                try:
                    browser.close()
                except Exception:
                    pass

                if pair:
                    self.result = pair
                    self._set("success", "Signed in to ESPN.")
                elif self.state != "cancelled":
                    self._set(
                        "error",
                        "Timed out waiting for sign-in. Try again, or use the "
                        "manual cookie option.",
                    )
        except Exception as e:
            self._set("error", f"Sign-in failed: {e}")

    def _launch(self, pw):
        """Launch the first available browser, preferring installed Chrome/Edge."""
        for channel in self._CHANNELS:
            try:
                kwargs = {"headless": False}
                if channel:
                    kwargs["channel"] = channel
                return pw.chromium.launch(**kwargs)
            except Exception:
                continue
        return None

    def _await_cookies(self, context):
        """Poll the browser context until both ESPN auth cookies exist."""
        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            if self.state == "cancelled":
                return None
            try:
                jar = {
                    c["name"]: c["value"]
                    for c in context.cookies()
                    if c["name"] in (_SWID, _S2)
                }
            except Exception:
                # Context torn down (user closed the window manually).
                return None
            pair = _pair_from(jar)
            if pair:
                # Give ESPN a beat to finish setting everything before we close.
                time.sleep(1.0)
                return pair
            time.sleep(self.poll_seconds)
        return None
