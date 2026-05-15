#!/usr/bin/env python3
"""
Eco Font Converter - Save up to 33% printer ink
Converts PDF, DOCX, and Markdown files to eco printing mode.

The eco mode uses PDF text rendering mode 1 (stroke-only) which draws only
the outlines of characters instead of filling them solid — the same technique
used by the Ryman Eco font.

Usage:
    python eco_font_converter.py input.pdf
    python eco_font_converter.py *.pdf *.docx
    python eco_font_converter.py --folder ./reports
    python eco_font_converter.py input.pdf --stroke-width 0.8 --output custom_name.pdf
    python eco_font_converter.py input.pdf --auto-install
"""

import argparse
import glob
import os
import re
import subprocess
import sys
import zlib
from pathlib import Path

# Force UTF-8 output on Windows so box-drawing characters don't crash
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ─── Dependencies ──────────────────────────────────────────────────────────────

def _pip_install(package: str):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package, "-q"])


def check_dep(module, package=None):
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def ensure_deps(required, auto_install=False):
    missing = [(mod, pkg) for mod, pkg in required if not check_dep(mod, pkg)]
    if not missing:
        return
    for mod, pkg in missing:
        if auto_install:
            print(f"  Installing {pkg}...")
            try:
                _pip_install(pkg)
                print(f"  ✓ {pkg} installed")
            except Exception as e:
                print(f"  ✗ Failed to install {pkg}: {e}")
                sys.exit(1)
        else:
            print(f"  Missing: {pkg}  →  pip install {pkg}")
    if not auto_install:
        print("\n[eco] Install missing packages and re-run, or use --auto-install (-y).")
        sys.exit(1)


# ─── Font paths ────────────────────────────────────────────────────────────────

def _find_font(candidates):
    for p in candidates:
        expanded = glob.glob(p)
        if expanded:
            return expanded[0]
    return None


def _font_candidates():
    if sys.platform == "win32":
        winfonts = os.environ.get("WINDIR", "C:\\Windows") + "\\Fonts\\"
        sans = [
            winfonts + "arial.ttf",
            winfonts + "calibri.ttf",
            winfonts + "segoeui.ttf",
            winfonts + "tahoma.ttf",
            winfonts + "verdana.ttf",
        ]
        mono = [
            winfonts + "consola*.ttf",
            winfonts + "cour.ttf",
            winfonts + "lucon.ttf",
        ]
    elif sys.platform == "darwin":
        sans = [
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        mono = [
            "/Library/Fonts/Courier New.ttf",
            "/System/Library/Fonts/Courier.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ]
    else:
        sans = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "/usr/share/fonts/*/DejaVuSans.ttf",
        ]
        mono = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
        ]
    return sans, mono


# ─── Font registration ─────────────────────────────────────────────────────────

_FONTS_REGISTERED = False
_ECO_SANS = "Helvetica"   # resolved name after registration
_ECO_MONO = "Courier"     # resolved name after registration


def _register_fonts():
    global _FONTS_REGISTERED, _ECO_SANS, _ECO_MONO
    if _FONTS_REGISTERED:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    sans_candidates, mono_candidates = _font_candidates()
    sans = _find_font(sans_candidates)
    mono = _find_font(mono_candidates)

    if sans:
        pdfmetrics.registerFont(TTFont("EcoSans", sans))
        _ECO_SANS = "EcoSans"
    else:
        print("  [warn] No sans-serif font found — falling back to Helvetica")

    if mono:
        pdfmetrics.registerFont(TTFont("EcoMono", mono))
        _ECO_MONO = "EcoMono"
    else:
        print("  [warn] No monospace font found — falling back to Courier")

    _FONTS_REGISTERED = True


# ═══════════════════════════════════════════════════════════════════════════════
#  PDF CONVERSION
# ═══════════════════════════════════════════════════════════════════════════════

def _inject_eco_into_stream(raw_bytes: bytes, stroke_width: float) -> bytes:
    sw = f"{stroke_width:.2f} w\n".encode()
    data = raw_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    data = re.sub(rb'\d+ Tr\s*\n?', b'', data)

    def wrap_bt(m):
        return b"1 Tr\n" + m.group(0) + b"\n0 Tr"

    data = re.sub(rb'BT\b.*?ET\b', wrap_bt, data, flags=re.DOTALL)
    return sw + data


def _process_stream_object(stream_obj, stroke_width: float):
    import pypdf.generic as gen

    raw = stream_obj.get_data()
    modified = _inject_eco_into_stream(raw, stroke_width)
    compressed = zlib.compress(modified)
    stream_obj._data = compressed
    stream_obj[gen.NameObject("/Filter")] = gen.NameObject("/FlateDecode")
    if "/Length" in stream_obj:
        stream_obj[gen.NameObject("/Length")] = gen.NumberObject(len(compressed))


def convert_pdf(input_path: str, output_path: str, stroke_width: float = 0.7):
    import pypdf
    import pypdf.generic as gen

    reader = pypdf.PdfReader(input_path)

    if reader.is_encrypted:
        print(f"  ✗ PDF is password-protected — cannot convert: {input_path}")
        raise ValueError("Encrypted PDF — provide an unlocked copy.")

    writer = pypdf.PdfWriter()
    total = len(reader.pages)

    for page_num, page in enumerate(reader.pages):
        print(f"  Processing page {page_num + 1}/{total}...", end="\r")

        if "/Contents" not in page:
            writer.add_page(page)
            continue

        contents_ref = page["/Contents"]
        contents_obj = contents_ref.get_object()

        if isinstance(contents_obj, gen.ArrayObject):
            for ref in contents_obj:
                _process_stream_object(ref.get_object(), stroke_width)
        else:
            _process_stream_object(contents_obj, stroke_width)

        writer.add_page(page)

    with open(output_path, "wb") as f:
        writer.write(f)

    print(f"\n  ✓ PDF converted ({total} pages) → {output_path}")


# ═══════════════════════════════════════════════════════════════════════════════
#  DOCX → ECO PDF
# ═══════════════════════════════════════════════════════════════════════════════

def _docx_runs_to_rl(para, safe=True):
    """Convert a paragraph's runs to a ReportLab XML string preserving bold/italic."""
    parts = []
    for run in para.runs:
        text = run.text
        if not text:
            continue
        if safe:
            text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if run.bold and run.italic:
            text = f"<b><i>{text}</i></b>"
        elif run.bold:
            text = f"<b>{text}</b>"
        elif run.italic:
            text = f"<i>{text}</i>"
        parts.append(text)
    return "".join(parts)


def _docx_table_to_story(table, styles):
    """Render a DOCX table as indented plain-text rows."""
    from reportlab.platypus import Paragraph, Spacer
    rows = []
    for row in table.rows:
        # python-docx repeats merged cell text for each spanned column — deduplicate
        seen_cells: set = set()
        cells = []
        for cell in row.cells:
            val = cell.text.strip()
            cell_id = id(cell._tc)
            if val and cell_id not in seen_cells:
                cells.append(val)
            seen_cells.add(cell_id)
        line = "  |  ".join(cells)
        if line:
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            rows.append(Paragraph(safe, styles["table"]))
    if rows:
        rows.append(Spacer(1, 4))
    return rows


def convert_docx(input_path: str, output_path: str, stroke_width: float = 0.7):
    from docx import Document
    from docx.text.paragraph import Paragraph as DocxParagraph
    from docx.table import Table as DocxTable
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    _register_fonts()

    doc = Document(input_path)
    rl_styles = getSampleStyleSheet()

    styles = {
        "h1":    ParagraphStyle("DH1",    parent=rl_styles["h1"],     fontName=_ECO_SANS, fontSize=18, spaceAfter=8),
        "h2":    ParagraphStyle("DH2",    parent=rl_styles["h2"],     fontName=_ECO_SANS, fontSize=14, spaceAfter=6),
        "h3":    ParagraphStyle("DH3",    parent=rl_styles["h3"],     fontName=_ECO_SANS, fontSize=12, spaceAfter=4),
        "body":  ParagraphStyle("DBody",  parent=rl_styles["Normal"], fontName=_ECO_SANS, fontSize=10, leading=14, spaceAfter=4),
        "table": ParagraphStyle("DTable", parent=rl_styles["Normal"], fontName=_ECO_MONO, fontSize=9,  leading=12, leftIndent=8, spaceAfter=2),
    }

    story = []

    for child in doc.element.body:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

        if tag == "p":
            para = DocxParagraph(child, doc)
            text_rl = _docx_runs_to_rl(para)
            if not text_rl.strip():
                story.append(Spacer(1, 6))
                continue
            style_name = para.style.name or "Normal"
            if "Heading 1" in style_name:
                pstyle = styles["h1"]
            elif "Heading 2" in style_name:
                pstyle = styles["h2"]
            elif "Heading 3" in style_name:
                pstyle = styles["h3"]
            else:
                pstyle = styles["body"]
            story.append(Paragraph(text_rl, pstyle))
            story.append(Spacer(1, 4))

        elif tag == "tbl":
            table = DocxTable(child, doc)
            story.extend(_docx_table_to_story(table, styles))

    tmp_path = output_path + ".tmp.pdf"
    try:
        doc_rl = SimpleDocTemplate(tmp_path, pagesize=letter,
                                    leftMargin=inch, rightMargin=inch,
                                    topMargin=inch, bottomMargin=inch)
        doc_rl.build(story)
        convert_pdf(tmp_path, output_path, stroke_width)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    print(f"  ✓ DOCX converted → {output_path}")


# ═══════════════════════════════════════════════════════════════════════════════
#  MARKDOWN → ECO PDF
# ═══════════════════════════════════════════════════════════════════════════════

def convert_markdown(input_path: str, output_path: str, stroke_width: float = 0.7):
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.platypus.flowables import HRFlowable

    _register_fonts()

    with open(input_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    rl_styles = getSampleStyleSheet()

    # Isolated styles — no mutation of the module-level singleton
    h1_style    = ParagraphStyle("MH1",    parent=rl_styles["h1"],     fontName=_ECO_SANS, fontSize=18, spaceAfter=8)
    h2_style    = ParagraphStyle("MH2",    parent=rl_styles["h2"],     fontName=_ECO_SANS, fontSize=14, spaceAfter=6)
    h3_style    = ParagraphStyle("MH3",    parent=rl_styles["h3"],     fontName=_ECO_SANS, fontSize=12, spaceAfter=4)
    body_style  = ParagraphStyle("MBody",  parent=rl_styles["Normal"], fontName=_ECO_SANS, fontSize=10, leading=14, spaceAfter=4)
    code_style  = ParagraphStyle("MCode",  parent=rl_styles["Code"],   fontName=_ECO_MONO, fontSize=9,  leading=12,
                                  backColor=colors.HexColor("#F5F5F5"), borderPadding=4)
    bullet_style = ParagraphStyle("MBullet", parent=body_style, leftIndent=20, bulletIndent=10)

    story = []
    lines = md_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("### "):
            story.append(Paragraph(_md_inline(line[4:], _ECO_MONO), h3_style))
        elif line.startswith("## "):
            story.append(Paragraph(_md_inline(line[3:], _ECO_MONO), h2_style))
        elif line.startswith("# "):
            story.append(Paragraph(_md_inline(line[2:], _ECO_MONO), h1_style))
        elif line.strip() in ("---", "***", "___"):
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
            story.append(Spacer(1, 4))
        elif line.startswith("```"):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            code_safe = "\n".join(code_lines).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(f"<pre>{code_safe}</pre>", code_style))
            story.append(Spacer(1, 6))
        elif line.startswith("- ") or line.startswith("* "):
            story.append(Paragraph("• " + _md_inline(line[2:], _ECO_MONO), bullet_style))
        elif re.match(r'^\d+\. ', line):
            story.append(Paragraph(_md_inline(re.sub(r'^\d+\. ', '', line), _ECO_MONO), body_style))
        elif line.strip() == "":
            story.append(Spacer(1, 6))
        else:
            story.append(Paragraph(_md_inline(line, _ECO_MONO), body_style))

        i += 1

    tmp_path = output_path + ".tmp.pdf"
    try:
        doc_rl = SimpleDocTemplate(tmp_path, pagesize=letter,
                                    leftMargin=inch, rightMargin=inch,
                                    topMargin=inch, bottomMargin=inch)
        doc_rl.build(story)
        convert_pdf(tmp_path, output_path, stroke_width)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    print(f"  ✓ Markdown converted → {output_path}")


def _md_inline(text: str, mono_font: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r'\*\*\*(.*?)\*\*\*', r'<b><i>\1</i></b>', text)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.*?)__', r'<b>\1</b>', text)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
    text = re.sub(r'`(.*?)`', rf'<font name="{mono_font}">\1</font>', text)
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
    return text


# ═══════════════════════════════════════════════════════════════════════════════
#  BATCH HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

SUPPORTED_EXTS = {".pdf", ".docx", ".md", ".markdown"}


def _resolve_inputs(raw_inputs, folder):
    """Return (supported_files, skipped_files)."""
    candidates = []

    if folder:
        folder_path = Path(folder)
        if not folder_path.is_dir():
            print(f"[eco] Folder not found: {folder}")
            sys.exit(1)
        candidates.extend(p for p in folder_path.iterdir() if p.is_file())

    for raw in raw_inputs:
        expanded = glob.glob(raw)
        if expanded:
            candidates.extend(Path(p) for p in expanded)
        else:
            p = Path(raw)
            if p.exists():
                candidates.append(p)
            else:
                print(f"[eco] Warning: not found — {raw}")

    seen = set()
    supported = []
    skipped = []
    for p in candidates:
        key = p.resolve()
        if key in seen:
            continue
        seen.add(key)
        if p.suffix.lower() in SUPPORTED_EXTS:
            supported.append(p)
        else:
            skipped.append(p)

    return supported, skipped


def _output_path_for(input_path, output_arg, output_dir):
    if output_arg:
        return output_arg
    stem = input_path.stem
    if output_dir:
        return str(Path(output_dir) / f"{stem}_eco.pdf")
    return str(input_path.parent / f"{stem}_eco.pdf")



# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Eco Font Converter — Save up to 33% printer ink",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python eco_font_converter.py report.pdf
  python eco_font_converter.py *.pdf *.docx
  python eco_font_converter.py --folder ./reports --output-dir ./eco_output
  python eco_font_converter.py notes.docx --stroke-width 0.9
  python eco_font_converter.py README.md --output README_eco.pdf
  python eco_font_converter.py doc.pdf --auto-install

Stroke width guide:
  0.4-0.6  Very light, maximum ink saving (may look faint)
  0.7      Default - good balance of readability and savings
  0.8-1.0  More visible strokes, less saving but clearer
        """
    )
    parser.add_argument("input", nargs="*", help="Input file(s) — .pdf, .docx, .md (supports globs)")
    parser.add_argument("--folder", "-f", help="Process all supported files in this folder")
    parser.add_argument("--output", "-o", help="Output PDF path (single-file mode only)")
    parser.add_argument("--output-dir", "-d", help="Output directory for batch mode")
    parser.add_argument("--stroke-width", "-s", type=float, default=0.7,
                        help="Stroke line width in PDF units (default: 0.7)")
    parser.add_argument("--auto-install", "-y", action="store_true",
                        help="Automatically install missing dependencies via pip")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be converted without doing it")

    args = parser.parse_args()

    if not (0.1 <= args.stroke_width <= 3.0):
        parser.error(f"--stroke-width must be between 0.1 and 3.0, got {args.stroke_width}")

    print(f"\n╔══════════════════════════════════════════════╗")
    print(f"║         Eco Font Converter — Save Ink        ║")
    print(f"╚══════════════════════════════════════════════╝\n")

    raw_inputs = list(args.input)
    auto_mode = not raw_inputs and not args.folder
    if auto_mode:
        print(f"  No input given — scanning current folder: {Path.cwd()}\n")
        # Inject cwd files as candidates via folder resolution
        args.folder = str(Path.cwd())

    files, skipped = _resolve_inputs(raw_inputs, args.folder)

    if skipped:
        print(f"  Skipping {len(skipped)} unsupported file(s):")
        for p in skipped:
            print(f"    • {p.name}  ({p.suffix or 'no extension'})")
        print()

    if not files:
        print("[eco] No supported files found.")
        print(f"      Supported formats: {', '.join(sorted(SUPPORTED_EXTS))}")
        sys.exit(1)

    if args.output_dir:
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    if args.output and len(files) > 1:
        print("[eco] --output can only be used with a single input file.")
        print("      Use --output-dir for batch mode.")
        sys.exit(1)

    print(f"  Files to convert : {len(files)}")
    print(f"  Stroke width     : {args.stroke_width}")
    if args.output_dir:
        print(f"  Output directory : {args.output_dir}")
    print()

    if args.dry_run:
        print("  [dry-run] Would convert:")
        for f in files:
            out = _output_path_for(f, args.output, args.output_dir)
            print(f"    {f}  →  {out}")
        print()
        return

    print("[1/3] Checking dependencies...")
    base_deps = [("pypdf", "pypdf"), ("reportlab", "reportlab")]
    ensure_deps(base_deps, auto_install=args.auto_install)

    suffixes = {f.suffix.lower() for f in files}
    if ".docx" in suffixes:
        ensure_deps([("docx", "python-docx")], auto_install=args.auto_install)
    # Note: .md conversion uses built-in parsing — no extra package needed

    print("  ✓ All dependencies available\n")

    results = []
    errors = []

    for idx, input_path in enumerate(files, 1):
        suffix = input_path.suffix.lower()
        output_path = _output_path_for(input_path, args.output, args.output_dir)

        print(f"[{idx}/{len(files)}] {input_path.name}")

        try:
            if suffix == ".pdf":
                convert_pdf(str(input_path), output_path, args.stroke_width)
            elif suffix == ".docx":
                convert_docx(str(input_path), output_path, args.stroke_width)
            elif suffix in (".md", ".markdown"):
                convert_markdown(str(input_path), output_path, args.stroke_width)

            in_size = os.path.getsize(str(input_path))
            out_size = os.path.getsize(output_path)
            results.append((input_path, output_path, in_size, out_size))

        except Exception as e:
            print(f"  ✗ Failed: {e}")
            import traceback
            traceback.print_exc()
            errors.append((input_path, str(e)))

        print()

    print("=" * 48)
    print(f"  Converted : {len(results)}/{len(files)} file(s)")
    if errors:
        print(f"  Errors    : {len(errors)}")
        for path, err in errors:
            print(f"    ✗ {path.name}: {err}")
    print()
    if results:
        total_in = sum(r[2] for r in results)
        total_out = sum(r[3] for r in results)
        print(f"  Total input size  : {total_in:,} bytes")
        print(f"  Total output size : {total_out:,} bytes")
        print()
        print("  ✓ Open the output PDF(s) to preview before printing.")
        print("  ✓ Estimated ink saving: ~33% vs solid-fill text")
    print()


if __name__ == "__main__":
    main()
