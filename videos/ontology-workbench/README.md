# Workbench explanation video

Korean, 1920×1080, 24 fps, 240 seconds. The published MP4, poster and captions live
under `docs/public/media/`. The explanation page is
`docs/14-demo/ontology-workbench.md`.

The footage is actual application UI driven by the real Workspace API with
synthetic data and in-memory test storage. Only the local rehearsal's Cognito
login is doubled. No customer PDF, meeting transcript, credential or live
Confluence content is included.

## Re-render existing assets

Install Node.js 22+, FFmpeg and Chromium. On Linux ARM64 set
`HYPERFRAMES_BROWSER_PATH` to the installed Chromium executable.

```bash
npm run check
npm run render -- --quality high --fps 24 --workers 4 --crf 21 --output final.mp4
ffprobe -v error -show_entries format=duration -of json final.mp4
```

The documentation player also supplies a VP9/Opus WebM for Chromium builds
without H.264/AAC support. Generate it from the verified MP4 before copying
both formats into `docs/public/media/`:

```bash
ffmpeg -i final.mp4 -c:v libvpx-vp9 -crf 28 -b:v 0 -deadline good \
  -cpu-used 5 -row-mt 1 -tile-columns 2 -threads 4 \
  -c:a libopus -b:a 96k ontology-workbench.webm
```

Allow disk space for temporary frames. Set `TMPDIR` to an adequately sized
workspace disk when `/tmp` is a small tmpfs.

## Regenerate source

1. Build the application with `npm run build` in `platform/web`.
2. Run `platform/web/test/capture-workbench.cjs` against the loopback-only helper.
   Require `assets/screens/capture-evidence.json` to report `passed`; never
   substitute fixture-rendered screenshots for this API-backed capture.
3. Install this directory's Python requirements in an isolated environment.
4. Edit `narration.json`. Delete only the changed segment's `.mp3` and `.json`
   speech-mark files under `assets/voice/`, then run `python synthesize.py`.
   This invokes AWS Polly for missing segments; AWS credentials stay outside
   the project. Unchanged voice files are reused.
5. Run `python build.py` to produce scenes, timed voice files, captions and
   chapter markers. Font regeneration uses the installed Noto CJK Korean fonts.
6. Run the pinned HyperFrames check, inspect all scene midpoints, and render.
7. Verify the actual duration, audio stream and playback. Update the public
   media files and complete content review before publication.

## Provenance

- Narration: project-authored Korean explanation, Amazon Polly Seoyeon neural.
- Audio timing: Polly speech marks, locally retimed with FFmpeg.
- UI captures: the project's synthetic rehearsal; no customer system capture.
- Diagrams: project-authored SVG, explicitly conceptual.
- Korean font subsets: Noto CJK Korean; license in `assets/fonts/LICENSE`.
- Animation runtime: GSAP 3.14.2; upstream license header retained in the local file.
- Composition: HyperFrames 0.8.36. `BRIEF.md`, `design.md` and `STORYBOARD.md`
  retain the production intent and scene structure.
