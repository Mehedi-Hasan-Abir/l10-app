# Demo video

`bash demo/record.sh` regenerates the silent, captioned demo video of the app (about 1:30) from scratch:

- It builds an isolated compose stack (`-p l10demo`, overlay `docker-compose.demo.yml`, no host ports, own volume),
  reseeds it, and runs a recorder container: two headless Chromium browsers (Blake on the left, Casey and later
  Ellis on the right) drive the real UI against `http://proxy` while Playwright records each one.
- `demo/recorder/compose.mjs` then uses ffmpeg to compose 1920x1080 / 30 fps H.264 with both panes, pane labels
  and burned-in captions. The same caption timeline becomes the `.srt`.
- Output in `demo/out/` (git-ignored): `l10-demo.mp4`, `l10-demo.srt`, `frames/*.png` (8 stills for review).
- The stack is always torn down (`down -v`) afterwards, and only the `l10demo` project is touched; your dev stack
  (`l10-app`) is never reseeded or stopped.
- Iterate faster: `KEEP=1 bash demo/record.sh` leaves the stack up; `RERUN=1 bash demo/record.sh` reuses it (reseeds
  first, the demo changes data). `DEMO_X264_PRESET=veryfast` speeds up encoding; `DEMO_KEEP_RAW=1` keeps the raw
  per-browser `.webm` files in `demo/out/raw`.
- To change the story or captions, edit `demo/recorder/record.mjs` (one `tl.caption(...)` per beat).
