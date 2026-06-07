# Overleaf sync

There are two practical ways to keep this paper with GitHub and Overleaf.
Overleaf documents both GitHub Synchronization and Git bridge. On Overleaf Cloud,
Git integration is a Premium feature, so availability depends on the account/project plan.

Official docs:

- https://docs.overleaf.com/integrations-and-add-ons/git-integration-and-github-synchronization
- https://docs.overleaf.com/integrations-and-add-ons/git-integration-and-github-synchronization/git
- https://docs.overleaf.com/integrations-and-add-ons/git-integration-and-github-synchronization/github-synchronization

## Option A: Overleaf GitHub Sync

Use this if the Overleaf project can be connected to GitHub from the Overleaf UI.

1. Push this repository to GitHub.
2. Import or sync `sa2shun/ccbench-wal` in Overleaf.
3. Set the main document to `paper/main.tex`.
4. Pull GitHub changes in Overleaf before editing.
5. Push Overleaf changes back to GitHub after editing.

This keeps code and paper in one GitHub repository. The tradeoff is that Overleaf sees the whole repository, not only `paper/`.

## Option B: Overleaf Git bridge with a paper subtree

Use this if you want Overleaf to contain only the LaTeX project files.

Add the Overleaf project as a second remote:

```sh
git remote add overleaf https://git.overleaf.com/PROJECT_ID
```

Push only `paper/` to Overleaf:

```sh
git subtree push --prefix=paper overleaf master
```

Pull edits made in Overleaf back into `paper/`:

```sh
git subtree pull --prefix=paper overleaf master --squash
```

Then commit the resulting `paper/` changes and push the normal branch to GitHub:

```sh
git add paper .gitignore
git commit -m "Add IPSJ paper project"
git push origin HEAD
```

Use this model when you want GitHub to remain the canonical repository while Overleaf remains a paper-only editor.
