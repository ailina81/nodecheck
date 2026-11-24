"""Генерация PDF-отчёта NodeCheck (кириллица через шрифт DejaVu из папки fonts/)."""
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

FONTS = Path(__file__).parent / "fonts"


def build_pdf(room, ufh, budget, rows, data, cheaper, mode) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_font("DV", "", str(FONTS / "DejaVuSans.ttf"))
    pdf.add_font("DV", "B", str(FONTS / "DejaVuSans-Bold.ttf"))
    pdf.add_page()
    W = pdf.epw

    def h(text, size=13):
        pdf.ln(3)
        pdf.set_font("DV", "B", size)
        pdf.multi_cell(W, 7, text, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DV", "", 10)

    def p(text, color=(0, 0, 0)):
        pdf.set_text_color(*color)
        pdf.multi_cell(W, 5.5, text, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    h("NodeCheck — отчёт ИИ-аудита отделочных материалов", 15)
    p(f"Дата: {datetime.now():%d.%m.%Y %H:%M}   ·   Режим: {mode}", (90, 90, 90))

    h("Исходные данные")
    p(f"Тип помещения: {room}\nТёплый пол: {ufh}\nБюджет: {budget}")
    for r in rows:
        p(f"• {r['Поверхность']}: {r['Материал']}")

    findings = data.get("findings", [])
    crit = sum(f.get("severity") == "critical" for f in findings)
    h("Итог проверки")
    if not findings:
        p("ЗЕЛЁНЫЙ — технических конфликтов не найдено", (20, 130, 60))
    else:
        p(f"КРАСНЫЙ ФЛАГ — критичных: {crit}, предупреждений: {len(findings) - crit}",
          (190, 30, 30) if crit else (180, 120, 0))
    if data.get("summary"):
        p(data["summary"])

    if findings:
        h("Найденные ошибки")
        for f in findings:
            crit_f = f.get("severity") == "critical"
            pdf.set_font("DV", "B", 10)
            p(("[КРИТИЧНО] " if crit_f else "[ВНИМАНИЕ] ") + f.get("title", ""),
              (190, 30, 30) if crit_f else (180, 120, 0))
            pdf.set_font("DV", "", 10)
            if f.get("item"):
                p(f.get("item"), (90, 90, 90))
            p(f.get("explanation", ""))
            if f.get("norm"):
                p("Норматив: " + f["norm"], (90, 90, 90))
            pdf.ln(2)

    recs = data.get("recommendations", [])
    if recs:
        h("Рекомендации по замене")
        for r in recs:
            p(f"• {r.get('replace', '')} → {r.get('suggestion', '')}")
            if r.get("why"):
                p("  " + r["why"], (90, 90, 90))

    if cheaper:
        h("Более дешёвые аналоги")
        for c in cheaper:
            p(f"• {c['item']} (сейчас: {c['current']})")
            p("  Варианты: " + "; ".join(c["options"]), (60, 60, 60))

    pdf.ln(4)
    p("Прототип. Результаты носят справочный характер — сверяйте с актуальной редакцией норм.", (120, 120, 120))
    return bytes(pdf.output())
