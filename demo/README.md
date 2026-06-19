# Demo dataset

This folder holds the **sample question bank** so anyone can clone the repo and
see every feature working. It is loaded by `python -m pipeline.seed_demo`.

## Files

| File | What it is |
|---|---|
| `demo_questions.json` | 20 real PMR past-exam questions (year 114) — stems & options only, from publicly available exam material. **Explanations are independently rewritten** (with textbook citations); the original copyrighted shared-notes explanations are **not** used. |
| `demo_questions_synthetic.json` | A few clearly-labeled **synthetic** questions (one image question, two multiple-select) to showcase those features. Not real exam content. |
| `images/expl_NN.png` | "PDF-style" page renders of each rewritten explanation (generated, not scanned). |
| `images/cmap_demo.png` | A fully synthetic schematic figure for the demo image question. |
| `build_demo_assets.py` | Regenerates the images from the JSON (`python demo/build_demo_assets.py`). |

## Licensing / privacy notes

- Explanations are independently written for study reference and **may contain
  errors** — they are not authoritative.
- All users and answer history produced by `seed_demo` are **synthetic** and
  correspond to no real examinee.
- This dataset contains **no** exam announcements, no "exam points," and no
  copyrighted explanation or textbook images.
