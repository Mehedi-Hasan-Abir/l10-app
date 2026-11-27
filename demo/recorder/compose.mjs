// Composes the final 1920x1080 video from the two raw Playwright recordings with ffmpeg:
//   [label strip + two panes (x0.75) side by side + caption band] | end card
// Captions, pane labels and the cards are all burned in with one libass (ASS) pass, so the same timeline also
// produces the standalone .srt. Runs standalone (`node recorder/compose.mjs`) against out/raw/timeline.json.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const OUT = process.env.DEMO_OUT_DIR ?? "/demo/out";
const RAW = path.join(OUT, "raw");
const FRAMES = path.join(OUT, "frames");
const FONT_DIR = "/usr/share/fonts/truetype/dejavu";
const FONT = "DejaVu Sans";

const W = 1920;
const H = 1080;
const LABEL_H = 48;
const PANE_W = 960;
const PANE_H = 825;
const BAND_Y = LABEL_H + PANE_H; // 873
const BAND_H = H - BAND_Y; // 207
const FPS = 30;
const TITLE_S = 0; // no title card (scope cut); every caption time is already relative to the main segment
const END_S = 2.5;

const END_LINES = [
  "docker compose up · bash scripts/test.sh",
  "ruff · 827 backend tests · two-browser Playwright E2E",
  "github.com/Mehedi-Hasan-Abir/l10-app",
];

const run = (cmd, args, opts = {}) => execFileSync(cmd, args, { stdio: ["ignore", "inherit", "inherit"], ...opts });

function assTime(sec) {
  const cs = Math.round(sec * 100);
  const h = Math.floor(cs / 360000);
  const m = Math.floor((cs % 360000) / 6000);
  const s = Math.floor((cs % 6000) / 100);
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(cs % 100).padStart(2, "0")}`;
}

function srtTime(sec) {
  const ms = Math.round(sec * 1000);
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const pad = (n, l = 2) => String(n).padStart(l, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)},${pad(ms % 1000, 3)}`;
}

/** Split into at most two balanced lines (the ASS style disables auto-wrap so layout is deterministic). */
function breakLines(text) {
  if (text.length <= 46) return [text];
  const words = text.split(" ");
  let best = 0;
  let bestDiff = Infinity;
  for (let i = 1; i < words.length; i++) {
    const diff = Math.abs(words.slice(0, i).join(" ").length - words.slice(i).join(" ").length);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = i;
    }
  }
  return [words.slice(0, best).join(" "), words.slice(best).join(" ")];
}

const assEscape = (s) => s.replace(/\\/g, "").replace(/[{}]/g, "");

export function compose(tl) {
  const mainS = (tl.tEnd - tl.tStart) / 1000;
  const total = TITLE_S + mainS + END_S;
  const aSeek = (tl.tStart - tl.tA0) / 1000;
  const bSeek = (tl.tStart - tl.tB0) / 1000;

  // ---- captions: each lasts until the next one starts (the last until the end of the main segment) -------------
  const caps = tl.captions.map((c, i) => ({
    text: c.text,
    start: TITLE_S + c.start / 1000,
    end: TITLE_S + (i + 1 < tl.captions.length ? tl.captions[i + 1].start : tl.tEnd - tl.tStart) / 1000,
  }));

  // ---- .srt -----------------------------------------------------------------------------------------------------
  const srt = caps.map((c, i) => `${i + 1}\n${srtTime(c.start)} --> ${srtTime(c.end)}\n${c.text}\n`).join("\n");
  fs.writeFileSync(path.join(OUT, "l10-demo.srt"), srt);

  // ---- .ass (burned in) -----------------------------------------------------------------------------------------
  const labelA = "A · Blake Brooks — facilitator";
  const labelBs = [{ at: 0, text: "B · Casey Cho — teammate" }, ...tl.labelsB];
  const events = [];
  const ev = (start, end, style, x, y, text) =>
    events.push(`Dialogue: 0,${assTime(start)},${assTime(end)},${style},,0,0,0,,{\\an5\\pos(${x},${y})}${text}`);

  // end card
  const e0 = TITLE_S + mainS;
  ev(e0, total, "EndBig", W / 2, 420, assEscape(END_LINES[0]));
  ev(e0, total, "EndMid", W / 2, 540, assEscape(END_LINES[1]));
  ev(e0, total, "EndLink", W / 2, 680, assEscape(END_LINES[2]));
  // pane labels
  ev(TITLE_S, TITLE_S + mainS, "Label", PANE_W / 2, LABEL_H / 2, assEscape(labelA));
  labelBs.forEach((l, i) => {
    const s = TITLE_S + l.at / 1000;
    const e = TITLE_S + (i + 1 < labelBs.length ? labelBs[i + 1].at : tl.tEnd - tl.tStart) / 1000;
    ev(s, e, "Label", PANE_W + PANE_W / 2, LABEL_H / 2, assEscape(l.text));
  });
  // captions, centred in the band
  for (const c of caps) {
    ev(c.start, c.end, "Cap", W / 2, BAND_Y + BAND_H / 2, breakLines(assEscape(c.text)).join("\\N"));
  }

  const ass = `[Script Info]
ScriptType: v4.00+
PlayResX: ${W}
PlayResY: ${H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,${FONT},50,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,60,60,0,1
Style: Label,${FONT},27,&H00F2E6D8,&H00F2E6D8,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,10,10,0,1
Style: TitleBig,${FONT},88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,10,10,0,1
Style: TitleSub,${FONT},46,&H00D9C4A8,&H00D9C4A8,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,10,10,0,1
Style: EndBig,${FONT},64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,10,10,0,1
Style: EndMid,${FONT},44,&H00D9C4A8,&H00D9C4A8,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,10,10,0,1
Style: EndLink,${FONT},52,&H00FFB86B,&H00FFB86B,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,10,10,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
${events.join("\n")}
`;
  fs.writeFileSync(path.join(RAW, "captions.ass"), ass);

  // ---- filter graph ---------------------------------------------------------------------------------------------
  const pane = (input, seek) =>
    `[${input}:v]fps=${FPS},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=4,` +
    `trim=start=${seek.toFixed(3)}:duration=${mainS.toFixed(3)},setpts=PTS-STARTPTS,` +
    `scale=${PANE_W}:${PANE_H}:flags=lanczos`;
  const bg = "0x101318";
  const graph = [
    `${pane(0, aSeek)}[a]`,
    `${pane(1, bSeek)}[b]`,
    `color=c=${bg}:s=${W}x${H}:r=${FPS}:d=${mainS.toFixed(3)}[bg]`,
    `[bg][a]overlay=x=0:y=${LABEL_H}[o1]`,
    `[o1][b]overlay=x=${PANE_W}:y=${LABEL_H}[o2]`,
    `[o2]drawbox=x=0:y=0:w=${W}:h=${LABEL_H}:color=0x1a212c:t=fill,` +
      `drawbox=x=0:y=${BAND_Y}:w=${W}:h=${BAND_H}:color=0x0b0d11:t=fill,` +
      `drawbox=x=${PANE_W - 1}:y=${LABEL_H}:w=2:h=${PANE_H}:color=0x2b3442:t=fill,` +
      `format=yuv420p,setsar=1[main]`,
    `color=c=${bg}:s=${W}x${H}:r=${FPS}:d=${END_S}[e0]`,
    `[e0]format=yuv420p,setsar=1[e]`,
    `[main][e]concat=n=2:v=1:a=0,` +
      `ass=${path.join(RAW, "captions.ass")}:fontsdir=${FONT_DIR},` +
      `fade=t=in:st=0:d=0.5,fade=t=out:st=${(total - 0.7).toFixed(3)}:d=0.7,fps=${FPS}[v]`,
  ].join(";\n");
  const scriptPath = path.join(RAW, "filter.txt");
  fs.writeFileSync(scriptPath, graph);

  const mp4 = path.join(OUT, "l10-demo.mp4");
  console.log(`composing: main ${mainS.toFixed(1)}s, total ${total.toFixed(1)}s (A seek ${aSeek.toFixed(2)}s, B seek ${bSeek.toFixed(2)}s)`);
  run("ffmpeg", [
    "-y",
    "-hide_banner",
    "-loglevel",
    "warning",
    "-stats",
    "-i",
    path.join(RAW, "A.webm"),
    "-i",
    path.join(RAW, "B.webm"),
    "-filter_complex_script",
    scriptPath,
    "-map",
    "[v]",
    "-an",
    "-c:v",
    "libx264",
    "-preset",
    process.env.DEMO_X264_PRESET ?? "slow",
    "-crf",
    process.env.DEMO_X264_CRF ?? "18",
    "-profile:v",
    "high",
    "-pix_fmt",
    "yuv420p",
    "-r",
    String(FPS),
    "-movflags",
    "+faststart",
    mp4,
  ]);

  // ---- stills for review ----------------------------------------------------------------------------------------
  fs.mkdirSync(FRAMES, { recursive: true });
  tl.stills.forEach((s, i) => {
    const at = Math.max(0, TITLE_S + s.at / 1000 - 0.35); // just before the next caption replaces this one
    const file = path.join(FRAMES, `${String(i + 1).padStart(2, "0")}-${s.name}.png`);
    run("ffmpeg", ["-y", "-hide_banner", "-loglevel", "error", "-ss", at.toFixed(3), "-i", mp4, "-frames:v", "1", file]);
  });
  if (process.env.DEMO_REVIEW === "1") {
    const review = path.join(OUT, "review");
    fs.rmSync(review, { recursive: true, force: true });
    fs.mkdirSync(review, { recursive: true });
    run("ffmpeg", ["-y", "-hide_banner", "-loglevel", "error", "-i", mp4, "-vf", "fps=1/5", path.join(review, "r%03d.png")]);
  }

  console.log("\nffprobe:");
  run("ffprobe", [
    "-v",
    "error",
    "-select_streams",
    "v:0",
    "-show_entries",
    "stream=codec_name,profile,width,height,r_frame_rate,pix_fmt:format=duration,size,bit_rate",
    "-of",
    "default=nw=1",
    mp4,
  ]);

  if (process.env.DEMO_KEEP_RAW !== "1") {
    for (const f of ["A.webm", "B.webm", "captions.ass", "filter.txt", "timeline.json"]) fs.rmSync(path.join(RAW, f), { force: true });
    try {
      fs.rmdirSync(RAW);
    } catch {
      /* not empty (failure screenshots): keep */
    }
  }
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  compose(JSON.parse(fs.readFileSync(path.join(RAW, "timeline.json"), "utf8")));
}
