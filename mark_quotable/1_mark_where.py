# src/hist_docx_evidence_marker.py
"""
Programme zum Markieren von belegpflichtigen Aussagen in geschichtswissenschaftlichen DOCX-Texten.

Dependencies:
    pip install python-docx openai vaderSentiment python-dotenv

Nutzung:
    1. OPENAI_API_KEY als Umgebungsvariable setzen.
    2. INPUT_DOCX / OUTPUT_DOCX unten anpassen.
    3. python src/hist_docx_evidence_marker.py ausführen.

Das Script:
    - Lädt eine .docx-Datei.
    - Schickt Absätze an ein GPT-Modell, das Sätze als "belegpflichtig" / "nicht belegpflichtig" klassifiziert.
    - Nutzt zusätzlich Sentiment-Analyse (VADER), um sehr emotionale / wertende Sätze ebenfalls zu markieren.
    - Schreibt eine neue .docx-Datei mit markierten Sätzen und ein JSON-Log der Entscheidungen.
    - Merkt sich die letzte Analyse im JSON und wertet bei Folgeläufen nur geänderte Sätze neu aus.
"""

import os
import json
import re
from typing import List, Dict, Any, Tuple, Optional

from docx import Document
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from openai import OpenAI
import dotenv

# .env laden und OpenAI-Client initialisieren
dotenv.load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError("OPENAI_API_KEY ist nicht gesetzt (Umgebungsvariable).")

client = OpenAI(api_key=api_key)

# -------------------------
# Konfiguration
# -------------------------

# Pfad zur Eingabe-/Ausgabedatei
INPUT_DOCX = "/Users/programming/PycharmProjects/Find_Bibliography_NEw/mark_quotable/BA-Arbeit-Prototype.docx"
OUTPUT_DOCX = "/Users/programming/PycharmProjects/Find_Bibliography_NEw/mark_quotable/BA-Arbeit-Prototype_output_marked.docx"
REPORT_JSON = "analysis_report.json"  # dient gleichzeitig als Cache

# OpenAI-Modell & Parameter
OPENAI_MODEL = "gpt-5.1"              # stärkeres, zuverlässigeres Modell
MAX_PARAGRAPH_CHARS = 8000           # zu lange Absätze werden abgebrochen/übersprungen

# Schwellenwert für "emotionale" Sätze (VADER compound-score)
SENTIMENT_THRESHOLD = 0.5

# Mindestlänge eines Absatzes, damit er überhaupt analysiert wird
MIN_PARAGRAPH_LENGTH = 40


# -------------------------
# Hilfsfunktionen
# -------------------------

def split_into_sentences(text: str) -> List[str]:
    """
    Sehr einfache deutsche Satzsegmentierung.
    Für deinen Anwendungsfall reicht i.d.R. eine Regex auf ., !, ? + Leerzeichen.

    Achtung: Abkürzungen (z.B. "z.B.", "u.a.") werden hierbei nicht perfekt behandelt,
    aber GPT kommt mit leicht schiefen Sätzen normalerweise klar.
    """
    text = text.strip()
    if not text:
        return []

    # Split nach Satzendezeichen + Leerraum
    parts = re.split(r'(?<=[.!?])\s+', text)
    # Filter leere
    sentences = [p.strip() for p in parts if p.strip()]
    return sentences


def build_gpt_prompt(sentences: List[str]) -> Tuple[str, str]:
    """
    Erstellt einen klaren System-/User-Prompt für die Klassifikation.
    Sprache: Deutsch, aber Modell kommt mit gemischten Texten zurecht.
    """
    # Wir nummerieren Sätze, damit das Modell stabil bleibt.
    numbered = []
    for idx, s in enumerate(sentences, start=1):
        numbered.append(f"{idx}. {s}")
    joined = "\n".join(numbered)

    instructions = (
        "Du bist ein Assistent für akademisches Schreiben im Fach Geschichte.\n"
        "Du bekommst einen Absatz, der in Einzelsätze zerlegt wurde.\n"
        "Aufgabe:\n"
        "  - Entscheide für jeden Satz, ob er in einer geschichtswissenschaftlichen "
        "    Arbeit normalerweise einen Beleg (Zitation) braucht.\n"
        "  - 'Belegpflichtig' sind vor allem:\n"
        "      * konkrete historische Behauptungen (Fakten über Personen, Ereignisse, Zahlen, Datierungen, Ursachen-Wirkungs-Ketten),\n"
        "      * zusammenfassende Aussagen über den Forschungsstand,\n"
        "      * wertende Gesamturteile über historische Entwicklungen.\n"
        "  - Keine Belege brauchen typischerweise:\n"
        "      * rein formale Sätze zur Struktur der Arbeit (z.B. 'Im Folgenden wird gezeigt ...'),\n"
        "      * rein methodische Hinweise (z.B. 'Diese Arbeit folgt einem mikrohistorischen Ansatz.'),\n"
        "      * ganz offensichtliche Trivialitäten.\n"
        "Gib NUR JSON zurück, im Format:\n"
        "[\n"
        "  {\"index\": <Satznummer>, \"needs_citation\": true/false},\n"
        "  ...\n"
        "]\n"
        "Keine sonstigen Texte, keine Erklärungen."
    )

    user = (
        "Hier sind die Sätze des Absatzes:\n\n"
        f"{joined}\n\n"
        "Klassifiziere bitte alle Sätze wie beschrieben."
    )

    return instructions, user


def call_gpt_classification(openai_client: OpenAI, sentences: List[str]) -> List[bool]:
    """
    Ruft das GPT-Modell auf und gibt pro Satz ein bool zurück: True = braucht Beleg.
    Bei Fehlern (API down etc.) wird konservativ 'False' für alle Sätze zurückgegeben.
    """
    if not sentences:
        return []

    system_text, user_text = build_gpt_prompt(sentences)

    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
            temperature=0.0,
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)

        # Map index → needs_citation, Standard: False
        flags = [False] * len(sentences)
        if isinstance(data, list):
            for item in data:
                try:
                    idx = int(item.get("index"))
                    needs = bool(item.get("needs_citation"))
                    if 1 <= idx <= len(sentences):
                        flags[idx - 1] = needs
                except Exception:
                    continue
        return flags
    except Exception as e:
        print(f"[WARN] GPT-Analyse fehlgeschlagen: {e}")
        return [False] * len(sentences)


def sentiment_flags(analyzer: SentimentIntensityAnalyzer, sentences: List[str]) -> List[bool]:
    """
    Sehr einfache Sentiment-Heuristik:
    - Nutzt VADER, um stark wertende/emotionale Sätze zu finden.
    - Wenn |compound| >= SENTIMENT_THRESHOLD → Flag.
    """
    flags = []
    for s in sentences:
        scores = analyzer.polarity_scores(s)
        compound = scores.get("compound", 0.0)
        flags.append(abs(compound) >= SENTIMENT_THRESHOLD)
    return flags


def mark_sentences(
    sentences: List[str],
    cite_flags: List[bool],
    emo_flags: List[bool],
) -> List[str]:
    """
    Fügt Markierung vor Sätze, die belegpflichtig und/oder emotional sind.

    Markierungsformat:
        ⟦CITATION?⟧ <Satz>
    oder
        ⟦CITATION?/SENTIMENT⟧ <Satz>

    Du kannst das Tag natürlich später an dein Layout anpassen.
    """
    marked = []
    for s, cite, emo in zip(sentences, cite_flags, emo_flags):
        if cite and emo:
            tag = "⟦CITATION?/SENTIMENT⟧ "
            marked.append(tag + s)
        elif cite:
            tag = "⟦CITATION?⟧ "
            marked.append(tag + s)
        elif emo:
            tag = "⟦SENTIMENT⟧ "
            marked.append(tag + s)
        else:
            marked.append(s)
    return marked


def load_previous_report(report_path: str) -> List[Dict[str, Any]]:
    """
    Lädt den letzten Analyse-Report (falls vorhanden), der als Cache dient.
    """
    if not os.path.exists(report_path):
        return []
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        print(f"[WARN] Konnte vorherigen Report nicht laden ({e}), starte ohne Cache.")
        return []


def build_previous_maps(
    prev_report: List[Dict[str, Any]]
) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """
    Erzeugt zwei Maps:
      - nach paragraph_index
      - nach original_text

    Damit können wir Absätze wiederfinden, auch wenn sich die Reihenfolge geändert hat.
    """
    by_index: Dict[int, Dict[str, Any]] = {}
    by_text: Dict[str, List[Dict[str, Any]]] = {}

    for entry in prev_report:
        # Map nach Index
        try:
            idx = int(entry.get("paragraph_index"))
            by_index[idx] = entry
        except Exception:
            pass

        # Map nach Original-Text
        original_text = entry.get("original_text", "")
        if original_text:
            by_text.setdefault(original_text, []).append(entry)

    return by_index, by_text


def compute_flags_with_cache(
    openai_client: OpenAI,
    analyzer: SentimentIntensityAnalyzer,
    sentences: List[str],
    prev_para_entry: Optional[Dict[str, Any]],
) -> Tuple[List[bool], List[bool]]:
    """
    Nutzt (falls vorhanden) die vorherige Analyse eines Absatzes, um nur geänderte Sätze
    neu zu scannen.

    Strategie:
      - Wenn kein vorheriger Eintrag: alle Sätze neu analysieren.
      - Wenn es einen vorherigen Eintrag gibt:
          * Baue Map text -> vorige Satzdaten.
          * Für jeden aktuellen Satz:
              - Wenn derselbe Satztext im Cache existiert: Flags übernehmen.
              - Sonst: Satz in die Liste der "neuen" Sätze aufnehmen.
          * Nur die neuen Sätze an GPT + Sentiment schicken.

    Ergebnis:
      - cite_flags und emo_flags enthalten für alle Sätze Werte,
        entweder übernommen oder neu berechnet.
    """
    if not sentences:
        return [], []

    prev_records = None
    if prev_para_entry:
        prev_records = prev_para_entry.get("sentences") or []

    # Kein Cache für diesen Absatz → alles neu analysieren
    if not prev_records:
        cite_flags = call_gpt_classification(openai_client, sentences)
        emo_flags = sentiment_flags(analyzer, sentences)
        return cite_flags, emo_flags

    # Map: Satztext -> Liste vorheriger Satz-Einträge (für Duplikate)
    prev_map: Dict[str, List[Dict[str, Any]]] = {}
    for rec in prev_records:
        text = rec.get("text", "")
        if text:
            prev_map.setdefault(text, []).append(rec)

    cite_flags: List[bool] = [False] * len(sentences)
    emo_flags: List[bool] = [False] * len(sentences)

    to_analyze: List[str] = []
    to_indices: List[int] = []

    # Versuche zuerst, Flags für unveränderte Sätze aus dem Cache zu übernehmen
    for i, s in enumerate(sentences):
        bucket = prev_map.get(s)
        if bucket:
            prev_rec = bucket.pop(0)
            cite_flags[i] = bool(prev_rec.get("needs_citation", False))
            emo_flags[i] = bool(prev_rec.get("sentiment_flag", False))
        else:
            # Neuer oder geänderter Satz → muss neu analysiert werden
            to_analyze.append(s)
            to_indices.append(i)

    # Nur geänderte Sätze an GPT + VADER schicken
    if to_analyze:
        new_cite = call_gpt_classification(openai_client, to_analyze)
        new_emo = sentiment_flags(analyzer, to_analyze)

        for local_idx, sent_idx in enumerate(to_indices):
            if local_idx < len(new_cite):
                cite_flags[sent_idx] = new_cite[local_idx]
            if local_idx < len(new_emo):
                emo_flags[sent_idx] = new_emo[local_idx]

    return cite_flags, emo_flags


# -------------------------
# Hauptlogik
# -------------------------

def process_docx(
    input_path: str,
    output_path: str,
    report_path: str,
) -> None:
    """
    Kernpipeline:
        - DOCX laden
        - Vorherigen Report (Cache) laden
        - Absätze iterieren
            * nur relevante Absätze/Sätze analysieren
            * soweit möglich Cache nutzen
        - Text neu setzen
        - neuen Report schreiben
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Eingabedatei nicht gefunden: {input_path}")

    # Sentiment-Analyzer und Cache vorbereiten
    analyzer = SentimentIntensityAnalyzer()
    prev_report = load_previous_report(report_path)
    prev_by_index, prev_by_text = build_previous_maps(prev_report)

    doc = Document(input_path)

    report: List[Dict[str, Any]] = []

    for p_idx, para in enumerate(doc.paragraphs):
        original_text = para.text or ""
        stripped = original_text.strip()

        if len(stripped) < MIN_PARAGRAPH_LENGTH:
            # Zu kurz, typischerweise Überschriften o.ä.
            continue

        if len(stripped) > MAX_PARAGRAPH_CHARS:
            # Optional: du könntest hier in zwei Hälften splitten.
            print(f"[INFO] Absatz {p_idx} übersprungen (zu lang für Analyse).")
            continue

        sentences = split_into_sentences(stripped)
        if not sentences:
            continue

        # Versuche, vorherige Analyse für diesen Absatz zu finden
        prev_entry: Optional[Dict[str, Any]] = prev_by_index.get(p_idx)

        # Falls sich die Absatz-Position geändert hat, aber der Text identisch ist,
        # nimm einen Eintrag mit demselben original_text aus der Text-Map.
        if prev_entry is None:
            same_text_entries = prev_by_text.get(original_text)
            if same_text_entries:
                prev_entry = same_text_entries.pop(0)

        # Flags entweder vollständig aus Cache oder nur für geänderte Sätze neu berechnen
        cite_flags, emo_flags = compute_flags_with_cache(
            client,
            analyzer,
            sentences,
            prev_entry,
        )

        marked_sentences = mark_sentences(sentences, cite_flags, emo_flags)
        new_text = " ".join(marked_sentences)

        para.text = new_text

        # Für die Nachvollziehbarkeit im JSON-Report speichern
        para_entry: Dict[str, Any] = {
            "paragraph_index": p_idx,
            "original_text": original_text,
            "sentences": [],
        }
        for idx, (s, cite, emo, marked) in enumerate(
            zip(sentences, cite_flags, emo_flags, marked_sentences), start=1
        ):
            para_entry["sentences"].append(
                {
                    "index": idx,
                    "text": s,
                    "needs_citation": cite,
                    "sentiment_flag": emo,
                    "marked_text": marked,
                }
            )
        report.append(para_entry)

    # DOCX speichern
    doc.save(output_path)
    print(f"[OK] Markierte DOCX geschrieben nach: {output_path}")

    # JSON-Report (Cache) speichern
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[OK] Analyse-Report (inkl. Cache) geschrieben nach: {report_path}")


if __name__ == "__main__":
    process_docx(INPUT_DOCX, OUTPUT_DOCX, REPORT_JSON)