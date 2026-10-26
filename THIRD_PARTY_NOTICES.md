# Third-party sources

- **Tacto Negotiation Arena Kit**, tacto-playground,
  https://github.com/tacto-playground/negotiation-arena-kit,
  pinned commit `01b7d127597e84cfcddca303b0f999ee05dff74e`, verified 25 October 2026.
  `vendor/arena-kit/` is MIT, with the original license included. Our `play.py` extension adds
  an optional recording-table factory, decision observer and economic close hook.
- **Tacto procurement data and challenge documentation**, CC BY 4.0, in `data/package/`
  and `docs/arena/`. Original notices and attribution are retained. Practice case JSON files
  were fetched from the authorized `GET /v1/cases?set=practice` endpoint on 24 October 2026.
  T01/T02 are synthetic scenarios defined in our code.
- **Vaccaro et al.**, arXiv:2503.06416v3, https://arxiv.org/abs/2503.06416. Research is
  paraphrased in `docs/RESEARCH.md`; the PDF is not redistributed here.
- Dependencies retain their licenses. Versions are pinned in `uv.lock`. Google Fonts serves
  DM Sans and Manrope, with system-font fallbacks.
