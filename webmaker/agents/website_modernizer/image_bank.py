"""
webmaker.agents.website_modernizer.image_bank
=============================================
Resolve real client images for Agent 1 page building.

Never invent filenames. Prefer CDN ``source_url`` (works in WP demo),
never emit local disk paths into HTML for the WordPress browser demo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from webmaker.core.logging import get_logger

log = get_logger("modernizer.images")

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


@dataclass
class ImageAsset:
    filename: str
    src: str          # URL or absolute path used in HTML
    local_path: str = ""
    source_url: str = ""
    alt: str = ""
    width: int = 0
    height: int = 0
    kind: str = "photo"  # logo | photo | other


@dataclass
class ImageBank:
    """Inventory of usable client images."""

    assets: list[ImageAsset] = field(default_factory=list)
    _used: set[str] = field(default_factory=set)

    @property
    def photos(self) -> list[ImageAsset]:
        return [a for a in self.assets if a.kind == "photo"]

    @property
    def logos(self) -> list[ImageAsset]:
        return [a for a in self.assets if a.kind == "logo"]

    def pick(
        self,
        *,
        prefer: str = "",
        role: str = "photo",
        avoid_used: bool = True,
        avoid_names: tuple[str, ...] = (),
        prefer_bright: bool = False,
    ) -> ImageAsset | None:
        """Pick one image. ``prefer`` may be a filename/url fragment."""
        pool = self.logos if role == "logo" else self.photos or self.assets
        if not pool:
            return None

        if avoid_names:
            filtered = [
                a for a in pool
                if not any(n.lower() in a.filename.lower() or n.lower() in a.src.lower()
                           for n in avoid_names)
            ]
            if filtered:
                pool = filtered

        # Prefer CDN / remote URLs for WordPress demo display
        def _score(a: ImageAsset) -> tuple:
            remote = 0 if a.src.startswith(("http://", "https://")) else 1
            area = -(a.width * a.height if a.width and a.height else 0)
            # Prefer julia/cameron style team/bright room names when requested
            bright = 0
            if prefer_bright:
                name = a.filename.lower()
                if any(k in name for k in ("julia", "cameron", "team", "truck", "room")):
                    bright = -1
                if any(k in name for k in ("print-3", "dustpan", "feedback")):
                    bright = 2
            return (bright, remote, area, a.filename)

        pool = sorted(pool, key=_score)

        if prefer:
            hit = self._match_prefer(pool, prefer, avoid_used=avoid_used)
            if hit is not None:
                self._used.add(hit.filename)
                return hit

        for a in pool:
            if avoid_used and a.filename in self._used:
                continue
            # Prefer CDN twin over local-only disk path
            if not a.src.startswith(("http://", "https://")):
                twin = self._cdn_twin(a, pool)
                if twin is not None:
                    if avoid_used and twin.filename in self._used:
                        continue
                    self._used.add(twin.filename)
                    return twin
            self._used.add(a.filename)
            return a
        return pool[0] if pool else None

    def _cdn_twin(self, asset: ImageAsset, pool: list[ImageAsset]) -> ImageAsset | None:
        key = _stem_key(asset.filename)
        for a in pool:
            if a is asset:
                continue
            if _stem_key(a.filename) == key and a.src.startswith(("http://", "https://")):
                return a
        return None

    def _match_prefer(
        self,
        pool: list[ImageAsset],
        prefer: str,
        *,
        avoid_used: bool,
    ) -> ImageAsset | None:
        pref = prefer.strip()
        pref_name = Path(pref.split("?")[0]).name.lower()
        pref_key = _stem_key(pref_name)
        candidates: list[ImageAsset] = []
        for a in pool:
            if avoid_used and a.filename in self._used:
                continue
            if prefer in (a.src, a.local_path, a.source_url, a.filename):
                candidates.append(a)
                continue
            if pref_key and _stem_key(a.filename) == pref_key:
                candidates.append(a)
                continue
            if pref_name and pref_name in a.filename.lower():
                candidates.append(a)
        if not candidates:
            return None
        # Prefer working CDN URLs over local disk paths (WP cannot load E:\…)
        candidates.sort(
            key=lambda a: (
                0 if a.src.startswith(("http://", "https://")) else 1,
                0 if (a.source_url or "").startswith(("http://", "https://")) else 1,
                a.filename,
            )
        )
        best = candidates[0]
        if not best.src.startswith(("http://", "https://")) and (best.source_url or "").startswith(
            ("http://", "https://")
        ):
            best.src = best.source_url
        return best

    def pick_many(self, n: int, *, role: str = "photo") -> list[ImageAsset]:
        out: list[ImageAsset] = []
        for _ in range(max(0, n)):
            a = self.pick(role=role, avoid_used=True)
            if a is None:
                break
            out.append(a)
        return out

    def resolve_src(self, ref: str) -> str:
        """Map a filename/fragment to a browser-loadable URL (prefer CDN)."""
        if not ref:
            return ""
        if ref.startswith(("http://", "https://")):
            return ref
        a = self.pick(prefer=ref, avoid_used=False)
        if a is None:
            return ""
        if a.src.startswith(("http://", "https://")):
            return a.src
        if (a.source_url or "").startswith(("http://", "https://")):
            return a.source_url
        # Local-only assets (e.g. custom hero BG) → copy into WP uploads
        local = a.local_path or (a.src if Path(a.src).is_file() else "")
        return publish_local_for_wp(local) if local else ""

    def filenames_for_prompt(self, limit: int = 16) -> list[str]:
        return [a.filename for a in self.assets[:limit]]


def _stem_key(name: str) -> str:
    """Normalize filenames so julia.jpg matches julia_1.jpg."""
    n = unquote(Path(str(name).split("?")[0]).name).lower()
    stem = Path(n).stem
    stem = re.sub(r"_\d+$", "", stem)
    return stem


def publish_local_for_wp(local_path: str | Path) -> str:
    """Copy a project image into WordPress uploads so the browser can load it."""
    import shutil

    src = Path(local_path)
    if not src.is_file():
        return ""
    try:
        from webmaker.config.settings import settings
    except Exception:
        return ""
    dest_dir = Path(settings.wordpress_dir) / "wp-content" / "uploads" / "webmaker"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if (
            not dest.exists()
            or dest.stat().st_size != src.stat().st_size
            or dest.stat().st_mtime < src.stat().st_mtime
        ):
            shutil.copy2(src, dest)
    except OSError as exc:
        log.warning("Could not publish local image {f}: {e}", f=src.name, e=exc)
        return ""
    return f"{settings.wordpress_url.rstrip('/')}/wp-content/uploads/webmaker/{src.name}"


def load_image_bank(data_dir: Path, package_dir: Path | None = None) -> ImageBank:
    """Build bank from website_package/assets.json + images/ folder."""
    data_dir = Path(data_dir)
    images_dir = data_dir / "images"
    if package_dir is None:
        package_dir = data_dir / "website_package"
        if not package_dir.is_dir():
            package_dir = data_dir.parent / "website_package"

    bank = ImageBank()
    seen: set[str] = set()
    seen_stems: set[str] = set()

    assets_json = Path(package_dir) / "assets.json" if package_dir else None
    if assets_json and assets_json.is_file():
        try:
            raw = json.loads(assets_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        for lo in raw.get("logo") or []:
            if isinstance(lo, dict):
                _add_from_dict(bank, seen, seen_stems, lo, kind="logo")
        for im in raw.get("images") or []:
            if isinstance(im, dict):
                _add_from_dict(bank, seen, seen_stems, im, kind="photo")

    if images_dir.is_dir():
        for f in sorted(images_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in _IMAGE_EXTS:
                continue
            if f.name in seen:
                continue
            # Skip local duplicate when CDN/package already has this photo stem
            stem = _stem_key(f.name)
            if stem in seen_stems:
                continue
            kind = "logo" if re.search(r"logo|1_1\.png|^1\.png$", f.name, re.I) else "photo"
            bank.assets.append(
                ImageAsset(
                    filename=f.name,
                    src=str(f.resolve()),
                    local_path=str(f.resolve()),
                    kind=kind,
                )
            )
            seen.add(f.name)
            seen_stems.add(stem)

    # Prefer larger photos / CDN first for hero
    bank.assets.sort(
        key=lambda a: (
            0 if a.kind == "photo" else 1,
            0 if a.src.startswith(("http://", "https://")) else 1,
            -(a.width * a.height if a.width and a.height else 0),
            a.filename,
        )
    )
    log.info("Image bank loaded — {n} assets ({p} photos)", n=len(bank.assets), p=len(bank.photos))
    return bank


def _add_from_dict(
    bank: ImageBank,
    seen: set[str],
    seen_stems: set[str],
    d: dict[str, Any],
    *,
    kind: str,
) -> None:
    filename = Path(str(d.get("filename") or d.get("local_path") or "")).name
    local = str(d.get("local_path") or "")
    url = str(d.get("source_url") or d.get("src") or "")
    if not filename and local:
        filename = Path(local).name
    if not filename and url:
        filename = Path(url.split("?")[0]).name
    if not filename or filename in seen:
        return

    # Prefer CDN URL for WP demo; else local absolute path
    src = url if url.startswith(("http://", "https://")) else ""
    if not src and local and Path(local).is_file():
        src = str(Path(local).resolve())
    if not src:
        return

    bank.assets.append(
        ImageAsset(
            filename=filename,
            src=src,
            local_path=local,
            source_url=url if url.startswith(("http://", "https://")) else "",
            alt=str(d.get("alt") or ""),
            width=int(d.get("width") or 0),
            height=int(d.get("height") or 0),
            kind=kind,
        )
    )
    seen.add(filename)
    seen_stems.add(_stem_key(filename))
