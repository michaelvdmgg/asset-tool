"""QR asset labels sized for Brother 12 mm TZe tape (PT-P710BT, 180 dpi)."""
import os
import sys
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

TAPE_PX = 64          # printable height on 12 mm tape at 180 dpi
PREVIEW_SCALE = 6     # preview PNG is scaled up so it reads on screen and in the video


def _font(px, bold=False):
    names = (["arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else ["arial.ttf", "DejaVuSans.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, px)
        except OSError:
            continue
    return ImageFont.load_default()


def make_label(url, key, lines, out_dir="labels"):
    """Returns (print_png, preview_png). print_png is 1:1 at 180 dpi; preview is enlarged."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=1, box_size=1)
    qr.add_data(url)
    qr.make(fit=True)
    q = qr.make_image(fill_color="black", back_color="white").convert("L")
    box = max(1, TAPE_PX // q.size[0])
    q = q.resize((q.size[0] * box, q.size[1] * box), Image.NEAREST)

    texts = [(key, _font(17, True))] + [(t, _font(12)) for t in lines[:3]]
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    text_w = max(probe.textlength(t, font=f) for t, f in texts)
    pad = 4
    w = pad + q.size[0] + 8 + int(text_w) + 6
    img = Image.new("L", (w, TAPE_PX), 255)
    img.paste(q, (pad, (TAPE_PX - q.size[1]) // 2))
    d = ImageDraw.Draw(img)
    y = 1
    for t, f in texts:
        d.text((pad + q.size[0] + 8, y), t, font=f, fill=0)
        y += f.size + 1

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{key}.png"
    img.convert("1").save(p, dpi=(180, 180))
    prev = out / f"{key}_preview.png"
    big = img.resize((w * PREVIEW_SCALE, TAPE_PX * PREVIEW_SCALE), Image.NEAREST)
    framed = Image.new("L", (big.size[0] + 40, big.size[1] + 40), 200)
    framed.paste(big, (20, 20))
    framed.save(prev)
    return p, prev


def print_label(png, printer):
    """Silent print through the Windows driver (pywin32). Untested until the P-touch arrives."""
    import win32print, win32ui            # noqa: E401  (pywin32, Windows only)
    from PIL import ImageWin
    img = Image.open(png)
    dc = win32ui.CreateDC()
    dc.CreatePrinterDC(printer)
    dc.StartDoc(Path(png).name)
    dc.StartPage()
    dpi_x = dc.GetDeviceCaps(88)           # LOGPIXELSX
    dpi_y = dc.GetDeviceCaps(90)           # LOGPIXELSY
    w, h = int(img.size[0] * dpi_x / 180), int(img.size[1] * dpi_y / 180)
    ImageWin.Dib(img.convert("RGB")).draw(dc.GetHandleOutput(), (0, 0, w, h))
    dc.EndPage()
    dc.EndDoc()
    dc.DeleteDC()


def open_file(path):
    if sys.platform.startswith("win"):
        os.startfile(path)                  # noqa: S606
