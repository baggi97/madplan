"""Destillerer DTU's Frida-regneark til den tabel appen bruger.

    python vaerktoej/lav_frida_tabel.py FCDB_6.1_Dataset.xlsx

Regnearket hentes fra https://fcdb.fooddata.dk/data — download-knappen. Det er
13 MB og ligger bevidst ikke i git; kun resultatet på ~90 kB gør. Kør værktøjet
igen når Frida udgiver en ny version.

Aliastabellen valideres mod datasættet, og værktøjet **fejler** hvis et navn
ikke findes. Uden det tjek rådner tabellen tavst: et forkert navn falder
tilbage på fuzzy-match, og så bliver 'mel' til 'melbanan' uden at nogen ser det.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Hverdagsmad trækker på ret få råvarer, og det er dem der bærer protein og
# kalorier. En håndholdt tabel over dem rammer det meste af vægten og fjerner
# gættet netop dér hvor en fejl ville flytte regnestykket mest.
#
# Nøglen er ordet som en opskrift skriver det. Værdien er Frida's eget navn,
# som SKAL findes i datasættet — ellers stopper værktøjet.
ALIAS = {
    # mejeri
    "mælk": "Letmælk, konventionel (ikke-økologisk)",
    "letmælk": "Letmælk, konventionel (ikke-økologisk)",
    "sødmælk": "Sødmælk, konventionel (ikke-økologisk)",
    "fløde": "Fløde 18 %",
    "piskefløde": "Fløde 38 %, piskefløde",
    "creme fraiche": "Creme fraiche 18 %",
    "smør": "Smør, usaltet",
    "revet ost": "Cheddar, 50+",
    "cheddar": "Cheddar, 50+",
    "feta": "Feta, 50+",
    "mozzarella": "Mozzarella, 45+",
    # kød og fisk — de bærer protein, så det er her et forkert opslag koster
    # mest. Frida navngiver systematisk men ikke som en opskrift skriver det:
    # en kotelet hedder "Grisekød, nakkefilet, helt afpudset (Nakkekotelet)".
    "hakket oksekød": "Oksekød, hakket, 8-12% fedt, rå",
    "oksekød": "Oksekød, hakket, 8-12% fedt, rå",
    "hakket svinekød": "Grisekød, hakket, 10-15% fedt, råt",
    "hakket gris": "Grisekød, hakket, 10-15% fedt, råt",
    "svinekød": "Grisekød, nakkefilet, helt afpudset (Nakkekotelet), rå",
    "grisekød": "Grisekød, nakkefilet, helt afpudset (Nakkekotelet), rå",
    "svinekotelet": "Grisekød, nakkefilet, helt afpudset (Nakkekotelet), rå",
    "nakkekotelet": "Grisekød, nakkefilet, helt afpudset (Nakkekotelet), rå",
    "kotelet": "Grisekød, nakkefilet, helt afpudset (Nakkekotelet), rå",
    "nakkefilet": "Grisekød, nakkefilet, helt afpudset (Nakkekotelet), rå",
    "svinemørbrad": "Grisekød, mørbrad, afpudset, rå",
    "grisemørbrad": "Grisekød, mørbrad, afpudset, rå",
    "oksemørbrad": "Oksekød, mørbrad, afpudset, rå",
    "kalvekød": "Kalvekød, helt magert, råt",
    "medisterpølse": "Medisterpølse, rå",
    "bacon": "Bacon i skiver, rå",
    "skinke": "Skinke, kogt, skiveskåret",
    "kyllingebryst": "Kylling, bryst, kød og skind, rå",
    "kyllingefilet": "Kylling, bryst, kød og skind, rå",
    "kyllingeinderfilet": "Kylling, bryst, kød og skind, rå",
    "kyllingelår": "Kylling, lår, kød og skind, rå",
    "kyllingelårfilet": "Kylling, lår, kød og skind, rå",
    "hakket kylling": "Kyllingekød, hakket, 3-10% fedt, rå",
    "hel kylling": "Kylling, kød og skind, rå",
    "laks": "Laks, atlantisk, opdræt, rå",
    "laksefilet": "Laks, atlantisk, opdræt, rå",
    "torsk": "Torsk, filet, rå",
    "torskefilet": "Torsk, filet, rå",
    "rødspætte": "Rødspætte, rå",
    "tun": "Tun i vand, konserves",
    "æg": "Æg, høne, frilands høns, rå",

    # kolonial
    "ris": "Ris, parboiled, rå",
    "pasta": "Pasta, rå",
    "bulgur": "Bulgur, parboiled, rå",
    "mel": "Hvedemel",
    "hvedemel": "Hvedemel",
    "olie": "Olivenolie",
    "olivenolie": "Olivenolie",
    "rapsolie": "Rapsolie",
    "sukker": "Sukker, stødt melis (sakkarose)",
    # grønt
    "løg": "Løg, rå",
    "hvidløg": "Hvidløg, rå",
    "kartofler": "Kartoffel, uspec., rå",
    "kartoffel": "Kartoffel, uspec., rå",
    "gulerødder": "Gulerod, dansk, rå",
    "gulerod": "Gulerod, dansk, rå",
    "spinat": "Spinat, rå",
    "broccoli": "Broccoli, rå",
    "porrer": "Porre, rå",
    "champignon": "Champignon, rå",
    "tomat": "Tomat, dansk, rå",
    "hakkede tomater": "Tomat, flået, konserves",
    "flåede tomater": "Tomat, flået, konserves",
    "persille": "Persille, rå",
    "citron": "Citron, rå",
}


def laes_regneark(sti: Path) -> list[dict]:
    import openpyxl  # kun et udviklingsværktøj — ikke i requirements.txt

    wb = openpyxl.load_workbook(sti, read_only=True, data_only=True)
    rader = wb["Data_Normalised"].iter_rows(values_only=True)
    kol = {navn: i for i, navn in enumerate(next(rader))}
    vil_have = {"Energi (kcal)": "kcal", "Protein": "protein"}

    mad: dict = {}
    for r in rader:
        felt = vil_have.get(r[kol["ParameterNavn"]])
        if not felt:
            continue
        post = mad.setdefault(r[kol["FoodID"]], {"navn": r[kol["FødevareNavn"]]})
        try:
            post[felt] = round(float(r[kol["ResVal"]]), 1)
        except (TypeError, ValueError):
            pass

    return [v for v in mad.values() if "kcal" in v and "protein" in v]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("brug: python vaerktoej/lav_frida_tabel.py <FCDB_x.y_Dataset.xlsx>")

    varer = laes_regneark(Path(sys.argv[1]))
    navne = {v["navn"] for v in varer}

    ukendte = {k: v for k, v in ALIAS.items() if v not in navne}
    if ukendte:
        print("Aliasnavne der ikke findes i datasættet:", file=sys.stderr)
        for nøgle, maal in sorted(ukendte.items()):
            forslag = [n for n in sorted(navne) if n.lower().startswith(nøgle.lower())][:3]
            if not forslag:
                forslag = [n for n in sorted(navne) if nøgle.lower() in n.lower()][:3]
            print("  %-16s -> %s" % (nøgle, maal), file=sys.stderr)
            if forslag:
                print("      mente du: %s" % " | ".join(forslag), file=sys.stderr)
        sys.exit(1)

    ud = Path(__file__).resolve().parent.parent / "app" / "frida.json"
    ud.write_text(
        json.dumps({"varer": varer, "alias": ALIAS}, ensure_ascii=False),
        encoding="utf-8",
    )
    print("%d fødevarer og %d alias -> %s (%.0f kB)" % (
        len(varer), len(ALIAS), ud.name, ud.stat().st_size / 1024))


if __name__ == "__main__":
    main()
