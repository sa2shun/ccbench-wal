# ccbench-wal paper

This directory is the LaTeX project root for Overleaf and GitHub.

## Layout

- `main.tex`: main IPSJ technical report source.
- `sections/`: body sections included from `main.tex`.
- `figures/`: committed paper figures. EPS is allowed here for IPSJ/pLaTeX.
- `tables/`: table sources or generated table snapshots.
- `refs.bib`: bibliography for the paper.
- `ipsj.cls`, `ipsjtech.sty`, `ipsjpref.sty`, `ipsjsort.bst`, `ipsjunsrt.bst`: IPSJ UTF-8 template files from `ipsj_v4-1.zip`.
- `template/`: original IPSJ technical-report sample for reference only.

## Build

Use pLaTeX + pBibTeX + dvipdfmx.

```sh
cd paper
latexmk main.tex
```

Overleaf should use `main.tex` as the main document. The `.latexmkrc` file in this directory configures the pLaTeX toolchain.

## GitHub and Overleaf

Recommended workflow:

1. Keep this repository as the source of truth for code, experiment scripts, results summaries, and paper sources.
2. In Overleaf, create/import a project from GitHub using this repository.
3. Set the Overleaf main document to `paper/main.tex` if the whole repository is imported, or to `main.tex` if only `paper/` is used as the Overleaf project root.
4. Pull/push through Overleaf's GitHub Sync for writing edits, and keep code/data changes in normal Git commits.

If using Overleaf's Git bridge instead of GitHub Sync, add the Overleaf project as a second Git remote locally and push only the paper tree, for example with `git subtree`.

See `OVERLEAF.md` for concrete commands.
