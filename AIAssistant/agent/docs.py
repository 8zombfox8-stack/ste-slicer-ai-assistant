# Text extraction from attached documents (technical specifications in .docx).

import re
import zipfile
from xml.etree import ElementTree

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx_text(path, limit=20000):
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    lines = []
    for paragraph in root.iter(_W + "p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == _W + "t" and node.text:
                parts.append(node.text)
            elif node.tag == _W + "tab":
                parts.append("\t")
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit]
