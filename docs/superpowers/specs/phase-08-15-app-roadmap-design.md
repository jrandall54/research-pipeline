# The app: roadmap and long-horizon goals

> **Phase 8–15** · Status: agreed; each phase gets its own design · Roadmap: [ROADMAP.md](../ROADMAP.md)

Date: 2026-11-25. Status: agreed with the owner in conversation; each project below gets its own design before it is built.

## What the app is for

The pipeline (Phases 1-7) works unseen: every night it collects papers, ranks them against your interests and summarizes the best. The app is how you and others **use** that material: read it, learn from it, and share it.

In the owner's words: a shared researching, interest-sharing and educational tool. **Not social media.** Everyone has their own curated research pipeline, and people can see what other people are getting into.

The owner's own use comes first ("build it right for me, then go from there"), but every project is designed with the long-horizon goals below in view, so nothing built early blocks them.

## Long-horizon goals

- **Your own feed.** The pipeline's processed material shown as an easy-to-read feed you scroll, not a list of raw text summaries.
- **Shared feeds.** Anyone can publish their feed (opt-in). A public directory lists published feeds; you follow whichever you like. Views: mine only, everyone I follow, or a custom mix (e.g. mine + my teacher's).
- **No interaction.** No comments, likes, messages or replies. Following is one-way, like a podcast: no approval needed, and publishers do not see who follows them.
- **Explanatory visuals.** Papers can appear as diagrams and short narrated explainer videos, generated from the summaries the pipeline already checked. They explain; they never bait clicks.
- **Learning and research.** Notes, capturing links, study material (concept map, quizzes, your teacher's layered approach) and later thesis help.
- **Local first, cloud allowed.** Everything works with local models. Anyone who wants to use Claude or another paid/cloud model can.
- **Works for everyone's hardware.** Club members have different machines; the CPU-only case must still work (slower, overnight).

## Decisions so far

| Topic | Decision | Source |
|---|---|---|
| What a feed holds | What your pipeline found and processed (ranked, summarized papers, later diagrams and videos), turned into posts. No hand-picking needed to share. | owner |
| What never leaves your computer | Votes, notes, shelf and your own work. Votes shape your feed by ranking, but are not published. Matches the existing local-only rule for `my_work/`. | Claude's proposal, owner agreed |
| Publishing | Opt-in. Once on, the feed is public. | owner |
| Directory | One shared list of published feeds; you browse it and follow whom you want. Each entry: a display name you pick plus your profile's topics, so people can browse by interest. Joining or leaving is one `rpipe` command or app switch (no file editing). Leaving removes you from the list; copies already downloaded cannot be pulled back, and the app says so before you publish. | owner (directory, opt-in); Claude's proposal for entry contents and mechanics, owner agreed |
| Storage | Likely no database server: a feed is a folder of files (post list + summaries + images + videos). Text and images fit free hosting; video (~10 GB/year at 10 posts/day) probably needs cheap file storage (DigitalOcean credits, free tiers). Compared properly in project 3's design. | Claude's estimate, not decided |
| Diagrams | The language model writes diagrams as code (flowcharts, method sketches, timelines): accurate, cheap, works on CPU. Image generators optional for illustrations, not the main tool. | Claude's proposal, owner agreed ("sounds interesting") |
| Videos | Narrated explainers: script from the summary, local text-to-speech voice, clips move through diagrams and key points. Not AI-generated film. | Claude's proposal, owner agreed |

## The projects, in order

Renumbered 2026-11-25 as phases, continuing the pipeline's numbering: project 1 = Phase 8, 2 = 9, 3 = 10, 4 = 11, 5 = 12, 6 = 13, 7 = 14, 8 = 15. `../ROADMAP.md` is the live list.

Each is its own design → plan → build cycle.

1. **The app itself, with your own feed.** A local web page to browse, vote and save. Everything else plugs into it. Built on `library_api` (see `docs/guide/architecture.md`).
2. **Diagrams** in the feed.
3. **Publishing, the directory and subscriptions.**
4. **Notes and link capture** (paste an article or repo link; the nightly run fetches it; `fetch_url` requests).
5. **Narrated videos.**
6. **Study material**: concept map linked to coursework, quizzes, the teacher's layered templates (`deep_analysis` requests).
7. **Thesis help**: literature search, gap finder, citation export (`lit_search` requests).
8. **"Point at anything" import**: any link, any format (video, PDF, podcast, site) turned into something the pipeline can process. A bigger version of project 4; its own project.

Order chosen because each step needs the one before it (sharing needs a feed; a feed is worth sharing once it explains). The owner may move study material up if coursework needs it.

## Constraints carried from the pipeline

- The app never runs a model itself; heavy work goes through the request list (`docs/guide/architecture.md`).
- Every user setting has an `rpipe` command or app control; nobody hand-edits files.
- Every waiting step shows progress.
- Every change ships with its `docs/guide/` page.
