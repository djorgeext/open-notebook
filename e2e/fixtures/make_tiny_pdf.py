"""Write e2e/fixtures/f0b_tiny.pdf: 1 page, known facts, no deps. Run: python3 e2e/fixtures/make_tiny_pdf.py"""

from pathlib import Path

LINES = [
    "Zorblat Field Notes",
    "The zorblat sensor measures quantum heart coherence at 47 hertz.",
    "Engineer Mirela Vantosk built the first zorblat prototype in Tallinn.",
    "Zorblat readings rise when the wearer climbs stairs.",
]


def build() -> bytes:
    text = "BT /F1 12 Tf 72 720 Td 16 TL\n"
    text += "".join(f"({line}) Tj T*\n" for line in LINES) + "ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(text)} >>\nstream\n{text}\nendstream".encode(),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


if __name__ == "__main__":
    p = Path(__file__).with_name("f0b_tiny.pdf")
    p.write_bytes(build())
    print(p, p.stat().st_size)
