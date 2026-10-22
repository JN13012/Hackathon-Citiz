import sys, zipfile, re, xml.etree.ElementTree as ET, pandas as pd
def lire(f, feuille_cible):
    z = zipfile.ZipFile(f); ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    ss = [("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"]))) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns)]
    wb = ET.fromstring(z.read("xl/workbook.xml")); rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid = {r.get("Id"): r.get("Target") for r in rels}
    cible = None
    for s in wb.find("m:sheets", ns):
        if s.get("name") == feuille_cible:
            cible = rid[s.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")]
    path = "xl/" + cible.lstrip("/").replace("xl/", "")
    rows = []
    for r in ET.fromstring(z.read(path)).iter("{%s}row" % ns["m"]):
        ligne = {}
        for c in r.findall("m:c", ns):
            col = re.match(r"[A-Z]+", c.get("r")).group(0); v = c.find("m:v", ns)
            if v is None: continue
            ligne[col] = ss[int(v.text)] if c.get("t") == "s" else v.text
        rows.append(ligne)
    return rows
if __name__ == "__main__":
    rows = lire(sys.argv[1], sys.argv[2]); print(len(rows)); print(rows[:8])
