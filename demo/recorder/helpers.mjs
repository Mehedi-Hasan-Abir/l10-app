// Helpers for driving two browsers on camera: a visible cursor with click ripples, gliding mouse moves,
// visible typing, smooth scrolling, and the caption timeline that becomes both the burned-in captions and the .srt.
import { expect } from "@playwright/test";

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------------------------------------------------------
// Cursor overlay (headless video shows no mouse). Injected into every page of a context with addInitScript.
// Appended to <html> once DOMContentLoaded fires (not <body>, so React hydration never touches it), follows
// `mousemove`, pulses a ripple on `mousedown`, and survives navigations by persisting its last position.
// ---------------------------------------------------------------------------------------------------------------
export function cursorInit() {
  const ID = "__demo_cursor";
  const KEY = "__demo_cursor_pos";
  let x = -100;
  let y = -100;
  try {
    const saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
    if (saved && typeof saved.x === "number") {
      x = saved.x;
      y = saved.y;
    }
  } catch {
    /* storage can be unavailable; the cursor then starts off screen */
  }

  let el = null;
  const place = () => {
    if (el) el.style.transform = `translate(${x}px, ${y}px)`;
  };
  const ensure = () => {
    if (!document.documentElement) return;
    if (el && el.isConnected) return;
    el = document.createElement("div");
    el.id = ID;
    el.setAttribute("aria-hidden", "true");
    el.style.cssText =
      "position:fixed;left:0;top:0;width:28px;height:28px;margin:0;padding:0;pointer-events:none;" +
      "z-index:2147483647;will-change:transform;";
    el.innerHTML =
      '<svg width="28" height="28" viewBox="0 0 24 24" style="display:block;overflow:visible;' +
      'filter:drop-shadow(0 1px 2px rgba(0,0,0,.5))"><path d="M3 2 L3 18.5 L7.4 14.7 L10.3 21.3 L13.2 20 ' +
      'L10.4 13.6 L16 13.6 Z" fill="#ffffff" stroke="#111111" stroke-width="1.7" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(el);
    place();
  };

  const ripple = (cx, cy) => {
    if (!document.documentElement) return;
    const dot = document.createElement("div");
    dot.setAttribute("aria-hidden", "true");
    dot.style.cssText =
      `position:fixed;left:${cx - 22}px;top:${cy - 22}px;width:44px;height:44px;box-sizing:border-box;` +
      "border-radius:50%;border:3px solid rgba(37,99,235,.95);background:rgba(37,99,235,.28);" +
      "pointer-events:none;z-index:2147483646;";
    document.documentElement.appendChild(dot);
    const anim = dot.animate(
      [
        { transform: "scale(.35)", opacity: 1 },
        { transform: "scale(1.5)", opacity: 0 },
      ],
      { duration: 520, easing: "ease-out" },
    );
    anim.onfinish = () => dot.remove();
  };

  window.addEventListener(
    "mousemove",
    (e) => {
      x = e.clientX;
      y = e.clientY;
      ensure();
      place();
      try {
        sessionStorage.setItem(KEY, JSON.stringify({ x, y }));
      } catch {
        /* ignore */
      }
    },
    { capture: true, passive: true },
  );
  window.addEventListener("mousedown", (e) => ripple(e.clientX, e.clientY), { capture: true, passive: true });

  const start = () => {
    ensure();
    // If a framework ever drops the node, put it back.
    new MutationObserver(() => ensure()).observe(document.documentElement, { childList: true });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
}

// ---------------------------------------------------------------------------------------------------------------
// Pointer helpers
// ---------------------------------------------------------------------------------------------------------------
const lastPos = new WeakMap();
const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);

/** Move the mouse along an eased path over a perceptible time (mouse.move alone teleports in <50 ms). */
export async function glide(page, x, y) {
  const from = lastPos.get(page) ?? { x: 900, y: 650 };
  const dist = Math.hypot(x - from.x, y - from.y);
  const duration = Math.min(1300, Math.max(650, dist * 1.6));
  const steps = Math.max(10, Math.round(duration / 20));
  for (let i = 1; i <= steps; i++) {
    const e = ease(i / steps);
    await page.mouse.move(from.x + (x - from.x) * e, from.y + (y - from.y) * e);
    await sleep(duration / steps);
  }
  lastPos.set(page, { x, y });
}

/** Wait until window scroll stops changing. */
async function settleScroll(page) {
  let last = -1;
  for (let i = 0; i < 40; i++) {
    const y = await page.evaluate(() => Math.round(window.scrollY));
    if (y === last) return;
    last = y;
    await sleep(110);
  }
}

/** Smoothly scroll the page so `top` is at the top of the viewport. */
export async function scrollPageTo(page, top) {
  await page.evaluate((t) => window.scrollTo({ top: t, behavior: "smooth" }), top);
  await sleep(120);
  await settleScroll(page);
}

/** Smoothly scroll so the element's top sits `offset` px below the viewport top (clears the sticky header). */
export async function scrollToElement(page, locator, offset = 76) {
  const top = await locator.evaluate((el, off) => el.getBoundingClientRect().top + window.scrollY - off, offset);
  await scrollPageTo(page, Math.max(0, Math.round(top)));
}

/** If the element is (mostly) off screen, bring it to the middle of the viewport with a smooth scroll. */
async function reveal(page, locator) {
  await locator.waitFor({ state: "visible" });
  const need = await locator.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const target = Math.round(r.top + r.height / 2 + window.scrollY - window.innerHeight / 2);
    return r.top < 80 || r.bottom > window.innerHeight - 24 ? target : null;
  });
  if (need !== null) await scrollPageTo(page, Math.max(0, need));
}

async function centerOf(locator, fx = 0.5, fy = 0.5) {
  const box = await locator.boundingBox();
  if (!box) throw new Error("element has no bounding box");
  return { x: box.x + box.width * fx, y: box.y + box.height * fy };
}

/** Glide the cursor onto the element and rest there (hover states and tooltips appear). */
export async function hoverVisibly(page, locator, { dwell = 900, fx = 0.5, fy = 0.5 } = {}) {
  await reveal(page, locator);
  const { x, y } = await centerOf(locator, fx, fy);
  await glide(page, x, y);
  await sleep(dwell);
}

/** scroll into view -> glide -> short pause -> click. Playwright's own click still checks the element is actionable. */
export async function clickVisibly(page, locator, { fx = 0.5, fy = 0.5 } = {}) {
  await reveal(page, locator);
  await expect(locator).toBeEnabled();
  const { x, y } = await centerOf(locator, fx, fy);
  await glide(page, x, y);
  await sleep(250);
  await locator.click({ position: await clickPosition(locator, fx, fy) });
  lastPos.set(page, { x, y });
}

async function clickPosition(locator, fx, fy) {
  const box = await locator.boundingBox();
  return { x: box.width * fx, y: box.height * fy };
}

/** Type like a person. */
export async function typeVisibly(locator, text, delay = 90) {
  await locator.pressSequentially(text, { delay });
}

/** A deliberate pause so viewers can read what just changed. */
export const beat = (ms = 1500) => sleep(ms);

// ---------------------------------------------------------------------------------------------------------------
// Caption timeline. Times are ms relative to `start()` (the first frame of the composed main segment).
// ---------------------------------------------------------------------------------------------------------------
export function createTimeline() {
  let t0 = 0;
  let prev = null;
  const captions = [];
  const labelsB = [];
  const stills = [];
  const rel = () => Date.now() - t0;

  return {
    start() {
      t0 = Date.now();
      return t0;
    },
    now: rel,
    /** Show a caption from now until the next one. Holds the script until the previous caption met its minimum. */
    async caption(text) {
      const words = text.trim().split(/\s+/).filter((w) => w !== "·").length;
      if (words > 6) console.warn(`caption has ${words} words (max 6): ${text}`);
      if (prev) {
        const wait = prev.start + prev.minMs - rel();
        if (wait > 0) await sleep(wait);
      }
      const entry = { start: rel(), text, minMs: Math.max(5500, Math.round(words * 300)) };
      captions.push(entry);
      prev = entry;
      console.log(`[${(entry.start / 1000).toFixed(1).padStart(6)}s] ${text}`);
    },
    /** Replace the right-hand pane's label (e.g. when browser B switches user). */
    labelB(text) {
      labelsB.push({ at: rel(), text });
    },
    /** Mark a moment to export as a still frame. Call it after the state to show is on screen. */
    still(name) {
      stills.push({ at: rel(), name });
      console.log(`[${(rel() / 1000).toFixed(1).padStart(6)}s] still: ${name}`);
    },
    /** Close the last caption: wait for its minimum on screen, then return the end time. */
    async finish() {
      if (prev) {
        const wait = prev.start + prev.minMs - rel();
        if (wait > 0) await sleep(wait);
      }
      return rel();
    },
    data() {
      return { captions, labelsB, stills };
    },
  };
}
