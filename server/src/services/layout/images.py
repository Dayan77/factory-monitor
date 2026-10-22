"""Plant floor-plan images (docs/sprint6.md §1.1): validation, SVG safety check and tenant file storage.

* PNG, JPEG or SVG only (detected from the content, never from the file name / client content type);
* at most settings.layout_image_max_bytes (10 MB) and settings.layout_image_max_side_px (8 000 px) on the
  larger side;
* SVG is refused (not "cleaned") when it holds anything active or external: <script>, on* attributes,
  <foreignObject>, <iframe>/<embed>/<object>, DOCTYPE/ENTITY declarations, javascript: URLs, href / xlink:href /
  url(...) / @import pointing outside the document (only "#fragment" and embedded data:image/png|jpeg are
  accepted);
* stored on the tenant file volume: FILES_DIR/<tenant_id>/layouts/<asset_id>.<ext> — never in the database.
"""
from __future__ import annotations

import re
import struct
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ...config import settings

CONTENT_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "svg": "image/svg+xml"}
EXT = {"png": "png", "jpeg": "jpg", "svg": "svg"}


class ImageError(ValueError):
    """Invalid image; `status` is the HTTP status to answer (413 too large, 422 otherwise)."""

    def __init__(self, msg: str, status: int = 422):
        super().__init__(msg)
        self.status = status


@dataclass
class ImageInfo:
    kind: str            # png | jpeg | svg
    width: int
    height: int
    size: int

    @property
    def content_type(self) -> str:
        return CONTENT_TYPES[self.kind]


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise ImageError("PNG inválido (cabeçalho IHDR ausente).")
    w, h = struct.unpack(">II", data[16:24])
    return w, h


_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg_size(data: bytes) -> tuple[int, int]:
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xD9:
            break
        seg = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in _SOF and i + 9 <= n:
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + seg
    raise ImageError("JPEG inválido (dimensões não encontradas).")


_NUM = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(px)?\s*$")
_URL = re.compile(r"url\s*\(\s*['\"]?\s*([^'\")\s]*)", re.I)
_SAFE_DATA = re.compile(r"^data:image/(png|jpeg|jpg);base64,", re.I)
_FORBIDDEN_TAGS = {"script", "foreignobject", "iframe", "embed", "object", "handler", "listener"}
_HREF_ATTRS = {"href", "src", "action", "formaction"}


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1].lower()


def _ref_ok(v: str) -> bool:
    v = (v or "").strip()
    return v == "" or v.startswith("#") or bool(_SAFE_DATA.match(v))


def check_svg(data: bytes) -> tuple[int, int]:
    """Refuses unsafe SVG (ImageError) and returns (width, height) in px (width/height, else the viewBox)."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImageError("SVG deve estar em UTF-8.")
    low = text.lower()
    if "<!doctype" in low or "<!entity" in low:
        raise ImageError("SVG recusado: declarações DOCTYPE/ENTITY não são permitidas.")
    if "javascript:" in low or "vbscript:" in low:
        raise ImageError("SVG recusado: URL javascript: não é permitida.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ImageError(f"SVG inválido: {e}")
    if _local(root.tag) != "svg":
        raise ImageError("Arquivo não é um SVG (raiz <svg> ausente).")
    for el in root.iter():
        tag = _local(el.tag)
        if tag in _FORBIDDEN_TAGS:
            raise ImageError(f"SVG recusado: elemento <{tag}> não é permitido.")
        for k, v in el.attrib.items():
            name = _local(k)
            if name.startswith("on"):
                raise ImageError(f"SVG recusado: atributo de evento '{name}' não é permitido.")
            if name in _HREF_ATTRS and not _ref_ok(v):
                raise ImageError("SVG recusado: referência externa não é permitida.")
            if name == "style" or "url(" in (v or "").lower():
                for ref in _URL.findall(v or ""):
                    if not _ref_ok(ref):
                        raise ImageError("SVG recusado: referência externa não é permitida.")
        if tag == "style":
            css = el.text or ""
            if "@import" in css.lower():
                raise ImageError("SVG recusado: @import não é permitido.")
            for ref in _URL.findall(css):
                if not _ref_ok(ref):
                    raise ImageError("SVG recusado: referência externa não é permitida.")
    w = _NUM.match(root.attrib.get("width", "") or "")
    h = _NUM.match(root.attrib.get("height", "") or "")
    if w and h:
        return int(round(float(w.group(1)))), int(round(float(h.group(1))))
    vb = (root.attrib.get("viewBox") or "").replace(",", " ").split()
    if len(vb) == 4:
        try:
            return int(round(float(vb[2]))), int(round(float(vb[3])))
        except ValueError:
            pass
    raise ImageError("SVG sem dimensões: informe width/height ou viewBox.")


def inspect(data: bytes) -> ImageInfo:
    size = len(data)
    if size == 0:
        raise ImageError("Arquivo vazio.")
    if size > settings.layout_image_max_bytes:
        raise ImageError(f"Imagem maior que {settings.layout_image_max_bytes // (1024 * 1024)} MB.", status=413)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        kind, (w, h) = "png", _png_size(data)
    elif data[:3] == b"\xff\xd8\xff":
        kind, (w, h) = "jpeg", _jpeg_size(data)
    elif b"<svg" in data[:4096].lower():
        kind, (w, h) = "svg", check_svg(data)
    else:
        raise ImageError("Formato não suportado: envie PNG, JPEG ou SVG.", status=415)
    if w <= 0 or h <= 0:
        raise ImageError("Imagem sem dimensões válidas.")
    if max(w, h) > settings.layout_image_max_side_px:
        raise ImageError(f"Lado maior da imagem acima de {settings.layout_image_max_side_px} px ({max(w, h)} px).")
    return ImageInfo(kind, w, h, size)


def tenant_dir(tenant_id: str) -> Path:
    tid = str(uuid.UUID(str(tenant_id)))          # never a path segment from user input
    return Path(settings.files_dir).resolve() / tid / "layouts"


def asset_path(tenant_id: str, asset_id: str, kind_or_ct: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", asset_id or ""):
        raise ImageError("asset inválido")
    kind = next((k for k, ct in CONTENT_TYPES.items() if ct == kind_or_ct), kind_or_ct)
    return tenant_dir(tenant_id) / f"{asset_id}.{EXT[kind]}"


def store(tenant_id: str, data: bytes, info: ImageInfo) -> str:
    asset_id = uuid.uuid4().hex
    path = asset_path(tenant_id, asset_id, info.kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return asset_id


def remove(tenant_id: str, asset_id: Optional[str], content_type: Optional[str]) -> None:
    if not asset_id or not content_type:
        return
    try:
        asset_path(tenant_id, asset_id, content_type).unlink(missing_ok=True)
    except (ImageError, OSError, KeyError):
        pass


def aspect_changed(old_w: Optional[int], old_h: Optional[int], new_w: int, new_h: int, tol: float = 0.02) -> bool:
    """Replacing the image by one whose proportion differs by more than 2 % → 'revisar posições'."""
    if not old_w or not old_h:
        return False
    a, b = old_w / old_h, new_w / new_h
    return abs(a - b) / a > tol
