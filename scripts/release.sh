#!/usr/bin/env bash
#
# Cut a release of reflex-desktop.
#
#   scripts/release.sh <version>        e.g. scripts/release.sh 0.3.0
#
# Bumps the version in pyproject.toml, verifies the build locally, commits, and
# tags v<version>. After a confirmation prompt it pushes the tag and creates the
# GitHub Release — publishing that release triggers .github/workflows/publish.yml,
# which uploads to PyPI via Trusted Publishing.
#
set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  echo "usage: $0 <version>   e.g. $0 0.3.0" >&2
  exit 1
fi
# X.Y.Z with an optional PEP 440 pre-release suffix (0.3.0rc1, 0.3.0b2, ...).
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?$ ]]; then
  echo "error: '$VERSION' is not a valid version (expected e.g. 0.3.0 or 0.3.0rc1)" >&2
  exit 1
fi

TAG="v$VERSION"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Guardrails: a clean tree on a fresh tag, so the release commit is exactly the bump.
if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: working tree is dirty — commit or stash first" >&2
  exit 1
fi
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "error: tag $TAG already exists" >&2
  exit 1
fi

# Bump the single version line in pyproject.toml.
python3 - "$VERSION" <<'PY'
import pathlib, re, sys
version = sys.argv[1]
path = pathlib.Path("pyproject.toml")
text, n = re.subn(r'(?m)^version = ".*"$', f'version = "{version}"', path.read_text(), count=1)
if n != 1:
    sys.exit(f"could not find a single version line in pyproject.toml (found {n})")
path.write_text(text)
PY

# Prove the distributions build and their metadata is valid before we tag anything.
rm -rf dist
uv build
uvx twine check dist/*

git add pyproject.toml
git commit -m "chore(release): $TAG"
git tag -a "$TAG" -m "$TAG"

echo
echo "Committed and tagged $TAG locally."
echo "Next: push the tag and create the GitHub Release (this publishes to PyPI)."
read -r -p "Push $TAG and create the release now? [y/N] " reply
if [[ ! "$reply" =~ ^[Yy]$ ]]; then
  echo "Stopped. To finish later:"
  echo "  git push origin HEAD $TAG"
  echo "  gh release create $TAG --title $TAG --generate-notes"
  exit 0
fi

git push origin HEAD "$TAG"
gh release create "$TAG" --title "$TAG" --generate-notes
echo "Released $TAG — watch the publish workflow: gh run watch"
