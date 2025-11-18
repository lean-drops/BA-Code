# src/hist_docx_evidence_marker.py
"""
Programme zum Markieren von belegpflichtigen Aussagen in geschichtswissenschaftlichen DOCX-Texten.

Dependencies:
    pip install python-docx openai vaderSentiment

Nutzung:
    1. OPENAI_API_KEY als Umgebungsvariable setzen.
    2. INPUT_DOCX / OUTPUT_DOCX unten anpassen.
    3. python src/hist_docx_evidence_marker.py ausführen.

Das Script:
    - Lädt eine .docx-Datei.
    - Schickt Absätze an ein GPT-Modell, das Sätze als "belegpflichtig" / "nicht belegpflichtig" klassifiziert.
    - Nutzt zusätzlich Sentiment-Analyse (VADER), um sehr emotionale / wertende Sätze ebenfalls zu markieren.
    - Schreibt eine neue .docx-Datei mit markierten Sätzen und ein JSON-Log der Entscheidungen.
"""

import os
import json
import re
from typing import List, Dict, Any

from docx import Document
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import os
from openai import OpenAI

import dotenv

dotenv.load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError("OPENAI_API_KEY ist nicht gesetzt (Umgebungsvariable).")

client = OpenAI(api_key=api_key)

# -------------------------
# Konfiguration
# -------------------------

# Pfad zur Eingabe-/Ausgabedatei
INPUT_DOCX = "/Users/programming/PycharmProjects/Find_Bibliography_NEw/doc/BA-Arbeit-Prototype.docx"
OUTPUT_DOCX = "output_marked.docx"
REPORT_JSON = "analysis_report.json"

# OpenAI-Modell & Parameter
OPENAI_MODEL = "gpt-4.1-mini"  # ggf. anpassen
MAX_PARAGRAPH_CHARS = 2000     # zu lange Absätze werden abgebrochen/übersprungen

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


def build_gpt_prompt(sentences: List[str]) -> str:
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

    # Wir geben System/User getrennt an, aber bauen Text nur in der API-Funktion zusammen.
    return instructions, user


def call_gpt_classification(client: OpenAI, sentences: List[str]) -> List[bool]:
    """
    Ruft das GPT-Modell auf und gibt pro Satz ein bool zurück: True = braucht Beleg.
    Bei Fehlern (API down etc.) wird konservativ 'False' für alle Sätze zurückgegeben.
    """
    if not sentences:
        return []

    system_text, user_text = build_gpt_prompt(sentences)

    try:
        response = client.chat.completions.create(
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
        - Absätze iterieren
        - GPT + Sentiment
        - Text neu setzen
        - Report schreiben
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Eingabedatei nicht gefunden: {input_path}")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY ist nicht gesetzt (Umgebungsvariable).")

    client = OpenAI(api_key=api_key)
    analyzer = SentimentIntensityAnalyzer()

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

        cite_flags = call_gpt_classification(client, sentences)
        emo_flags = sentiment_flags(analyzer, sentences)

        marked_sentences = mark_sentences(sentences, cite_flags, emo_flags)
        new_text = " ".join(marked_sentences)

        para.text = new_text

        # Für die Nachvollziehbarkeit im JSON-Report speichern
        para_entry = {
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

    # JSON-Report speichern
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[OK] Analyse-Report geschrieben nach: {report_path}")


if __name__ == "__main__":
    process_docx(INPUT_DOCX, OUTPUT_DOCX, REPORT_JSON)